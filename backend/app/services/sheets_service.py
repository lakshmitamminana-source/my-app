"""Google Sheets helpers."""

from __future__ import annotations

import json
import re

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound
from langchain_experimental.agents import create_pandas_dataframe_agent
from langchain_openai import ChatOpenAI
import pandas as pd

from app.core.settings import settings

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def _extract_sheet_id(sheet_url_or_id: str) -> str:
    value = (sheet_url_or_id or "").strip()
    if not value:
        raise ValueError("Google Sheet URL or ID is required")

    match = _SHEET_ID_RE.search(value)
    if match:
        return match.group(1)

    if re.fullmatch(r"[a-zA-Z0-9-_]+", value):
        return value

    raise ValueError("Invalid Google Sheet URL or ID")


def _build_gspread_client() -> gspread.Client:
    raw_json = (settings.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")

    try:
        account_info = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_JSON is invalid JSON: {exc}") from exc

    try:
        return gspread.service_account_from_dict(account_info)
    except Exception as exc:
        msg = str(exc).strip() or exc.__class__.__name__
        raise ValueError(f"Failed to initialize Google Sheets client: {msg}") from exc


def _build_sheet_llm() -> ChatOpenAI:
    """Build chat model for sheet question answering via LiteLLM proxy."""
    base_url = settings.LITELLM_PROXY_URL.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    return ChatOpenAI(
        model=settings.LITELLM_CHAT_MODEL,
        api_key=settings.LITELLM_API_KEY,
        base_url=base_url,
        temperature=0,
        timeout=settings.LITELLM_TIMEOUT_SECONDS,
        max_retries=settings.LITELLM_MAX_RETRIES,
    )


def load_sheet_as_dataframe(
    sheet_url_or_id: str,
    worksheet_name: str | None = None,
    worksheet_index: int | None = None,
) -> pd.DataFrame:
    """Load worksheet from Google Sheets into a DataFrame."""
    client = _build_gspread_client()
    sheet_id = _extract_sheet_id(sheet_url_or_id)

    try:
        spreadsheet = client.open_by_key(sheet_id)
    except SpreadsheetNotFound as exc:
        raise ValueError(
            "Spreadsheet not found or not shared with the service account. "
            "Share the sheet with the service account email as Viewer or higher."
        ) from exc
    except APIError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 403:
            raise ValueError(
                "Google API returned 403. Ensure Google Drive API and Google Sheets API are enabled "
                "for your project and the sheet is shared with the service account."
            ) from exc
        raise ValueError(f"Google API error while opening spreadsheet: {exc}") from exc
    except PermissionError as exc:
        # gspread may wrap underlying APIError(403/404) as PermissionError.
        cause = exc.__cause__
        if isinstance(cause, APIError):
            status_code = getattr(getattr(cause, "response", None), "status_code", None)
            if status_code == 403:
                raise ValueError(
                    "Google Sheets API returned 403. Enable Sheets/Drive APIs for the project and "
                    "share the sheet with the service account email."
                ) from exc
            raise ValueError(f"Google API error while opening spreadsheet: {cause}") from exc

        msg = str(exc).strip() or exc.__class__.__name__
        raise ValueError(f"Permission error while opening spreadsheet: {msg}") from exc
    except Exception as exc:
        msg = str(exc).strip() or exc.__class__.__name__
        raise ValueError(f"Unexpected error while opening spreadsheet: {msg}") from exc

    if worksheet_name:
        worksheet = spreadsheet.worksheet(worksheet_name)
    else:
        idx = worksheet_index if worksheet_index is not None else 0
        worksheet = spreadsheet.get_worksheet(idx)

    if worksheet is None:
        raise ValueError("No worksheet found in spreadsheet")

    try:
        values = worksheet.get_all_values()
    except APIError as exc:
        raise ValueError(f"Google API error while reading worksheet values: {exc}") from exc
    except Exception as exc:
        msg = str(exc).strip() or exc.__class__.__name__
        raise ValueError(f"Unexpected error while reading worksheet values: {msg}") from exc

    if not values:
        return pd.DataFrame()

    headers = values[0]
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers)


def load_sheet_preview(
    sheet_url_or_id: str,
    max_rows: int = 25,
    worksheet_name: str | None = None,
    worksheet_index: int | None = None,
) -> dict:
    """Load sheet and return a preview payload for frontend tools."""
    df = load_sheet_as_dataframe(
        sheet_url_or_id,
        worksheet_name=worksheet_name,
        worksheet_index=worksheet_index,
    )
    if df.empty:
        return {
            "row_count": 0,
            "columns": [],
            "rows": [],
        }

    preview_df = df.head(max_rows).fillna("")
    return {
        "row_count": int(len(df.index)),
        "columns": [str(col) for col in df.columns.tolist()],
        "rows": preview_df.astype(str).to_dict(orient="records"),
    }


def ask_sheet_question(
    sheet_url_or_id: str,
    question: str,
    user_email: str,
    worksheet_name: str | None = None,
    worksheet_index: int | None = None,
) -> dict:
    """Answer natural-language questions over a Google Sheet using pandas agent."""
    df = load_sheet_as_dataframe(
        sheet_url_or_id,
        worksheet_name=worksheet_name,
        worksheet_index=worksheet_index,
    )
    if df.empty:
        return {
            "answer": "The selected Google Sheet is empty.",
            "row_count": 0,
            "columns": [],
            "rows": [],
        }

    llm = _build_sheet_llm()
    agent = create_pandas_dataframe_agent(
        llm,
        df,
        verbose=False,
        allow_dangerous_code=True,
        return_intermediate_steps=True,
        max_iterations=25,
        max_execution_time=60,
        agent_executor_kwargs={"handle_parsing_errors": True},
    )
    prompt = (
        "Answer the user question using the dataframe. "
        "Return the answer in Markdown format. "
        "Do not return only a count/number. Include the actual values behind the result. "
        "Use short markdown sections when helpful, bullet points for highlights, and a markdown table "
        "with up to 10 rows when a table clarifies the answer.\n\n"
        f"Question: {question}"
    )
    result = agent.invoke(
        {"input": prompt},
        config={"metadata": {"user_email": user_email}},
    )

    answer = str(result.get("output") or "").strip() or "I could not find an answer in the sheet."
    preview_df = df.head(20).fillna("")
    return {
        "answer": answer,
        "row_count": int(len(df.index)),
        "columns": [str(col) for col in df.columns.tolist()],
        "rows": preview_df.astype(str).to_dict(orient="records"),
    }
