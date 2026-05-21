"""DataFrame query service for CSV/XLSX/Google Sheets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from langchain_experimental.agents import create_pandas_dataframe_agent
from langchain_openai import ChatOpenAI
import pandas as pd

from app.core.settings import settings
from app.schemas import MessageTurn
from app.services.sheets_service import load_sheet_as_dataframe

SourceType = Literal["csv", "xlsx", "google_sheet"]

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ALLOWED_LOCAL_ROOTS = (
    _PROJECT_ROOT,
    _PROJECT_ROOT / "uploads",
    _PROJECT_ROOT / "backend" / "uploads",
)


class DataframeQueryService:
    """Service for natural-language Q&A over tabular data sources."""

    def _build_llm(self) -> ChatOpenAI:
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

    @staticmethod
    def _resolve_local_path(source: str) -> Path:
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = (_PROJECT_ROOT / path).resolve()
        else:
            path = path.resolve()

        if not any(path.is_relative_to(root.resolve()) for root in _ALLOWED_LOCAL_ROOTS):
            raise ValueError("Local file path is outside allowed directories")

        if not path.exists() or not path.is_file():
            raise ValueError(f"Local file not found: {path}")

        return path

    def _load_dataframe(
        self,
        *,
        source_type: SourceType,
        source: str,
        worksheet_name: str | None,
        worksheet_index: int | None,
    ) -> pd.DataFrame:
        if source_type == "google_sheet":
            return load_sheet_as_dataframe(
                source,
                worksheet_name=worksheet_name,
                worksheet_index=worksheet_index,
            )

        file_path = self._resolve_local_path(source)
        suffix = file_path.suffix.lower()

        if source_type == "csv":
            if suffix != ".csv":
                raise ValueError("source_type 'csv' requires a .csv file")
            return pd.read_csv(file_path)

        if source_type == "xlsx":
            if suffix not in {".xlsx", ".xls"}:
                raise ValueError("source_type 'xlsx' requires a .xlsx or .xls file")
            sheet_ref: str | int = worksheet_name if worksheet_name else (worksheet_index or 0)
            return pd.read_excel(file_path, sheet_name=sheet_ref)

        raise ValueError("Unsupported source_type. Use csv, xlsx, or google_sheet")

    @staticmethod
    def _history_prefix(history: list[MessageTurn]) -> str:
        if not history:
            return ""
        turns = [f"{turn.role}: {turn.content}" for turn in history[-8:]]
        return "Conversation history:\n" + "\n".join(turns) + "\n\n"

    @staticmethod
    def _serialize_intermediate_steps(steps: list[Any]) -> list[str]:
        serialized: list[str] = []
        for idx, step in enumerate(steps):
            try:
                serialized.append(f"Step {idx + 1}: {step}")
            except Exception:
                serialized.append(f"Step {idx + 1}: <unserializable>")
        return serialized

    def query(
        self,
        *,
        source_type: SourceType,
        source: str,
        question: str,
        user_email: str,
        worksheet_name: str | None = None,
        worksheet_index: int | None = None,
        history: list[MessageTurn] | None = None,
        preview_rows: int = 20,
        include_table: bool = True,
    ) -> dict[str, Any]:
        history = history or []
        df = self._load_dataframe(
            source_type=source_type,
            source=source,
            worksheet_name=worksheet_name,
            worksheet_index=worksheet_index,
        )

        if df.empty:
            return {
                "answer": "The dataset is empty.",
                "source_type": source_type,
                "row_count": 0,
                "columns": [],
                "rows": [],
                "intermediate_steps": [],
            }

        llm = self._build_llm()
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
            self._history_prefix(history)
            + "Use only the provided dataframe to answer the user. "
            + "Return the answer in Markdown format. "
            + "Return a concise answer and include key numbers when relevant. "
            + "Do not respond with only a number/count; include the actual values behind the result. "
            + "Use short markdown sections or bullet points when helpful. "
            + "When helpful, include a markdown table with up to 10 relevant rows.\n\n"
            + f"Question: {question}"
        )
        result = agent.invoke(
            {"input": prompt},
            config={"metadata": {"user_email": user_email}},
        )

        answer = str(result.get("output") or "").strip() or "I could not answer the question from the dataset."
        steps = self._serialize_intermediate_steps(result.get("intermediate_steps") or [])

        preview_df = df.head(preview_rows).fillna("")
        return {
            "answer": answer,
            "source_type": source_type,
            "row_count": int(len(df.index)),
            "columns": [str(col) for col in df.columns.tolist()],
            "rows": preview_df.astype(str).to_dict(orient="records") if include_table else [],
            "intermediate_steps": steps,
        }


_dataframe_query_service: DataframeQueryService | None = None


def get_dataframe_query_service() -> DataframeQueryService:
    """Get singleton dataframe query service."""
    global _dataframe_query_service
    if _dataframe_query_service is None:
        _dataframe_query_service = DataframeQueryService()
    return _dataframe_query_service
