"""Quick validation for Google Sheets prerequisites.

Usage:
  /home/lakshmit/my-app/.venv/bin/python test_google_sheets_setup.py

Optional env vars:
  GOOGLE_SHEET_ID=<spreadsheet id>
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import dotenv_values
import gspread


def _fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


def _ok(message: str) -> None:
    print(f"[OK] {message}")


def main() -> int:
    env_values = dotenv_values(".env")
    raw_json = (env_values.get("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()

    if not raw_json:
        return _fail("GOOGLE_SERVICE_ACCOUNT_JSON is missing")

    if not (raw_json.startswith("{") and raw_json.endswith("}")):
        return _fail(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not a full JSON string. "
            "Store it as one valid JSON object string, not a file path or broken multiline value."
        )

    try:
        creds = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return _fail(f"GOOGLE_SERVICE_ACCOUNT_JSON is invalid JSON: {exc}")

    if not creds.get("client_email"):
        return _fail("Service account JSON missing client_email")
    _ok("Service account JSON includes client_email")

    if not creds.get("private_key"):
        return _fail("Service account JSON missing private_key")
    _ok("Service account JSON includes private_key")

    try:
        gc = gspread.service_account_from_dict(creds)
    except Exception as exc:  # pragma: no cover - runtime validation script
        return _fail(f"gspread auth failed: {type(exc).__name__}: {exc}")

    _ok("gspread.service_account_from_dict() authentication succeeded")

    try:
        files = gc.list_spreadsheet_files()
        _ok(f"Drive API reachable. Visible spreadsheet files: {len(files)}")
    except Exception as exc:  # pragma: no cover - runtime validation script
        return _fail(
            "Drive API call failed. Ensure Drive API is enabled and network can reach googleapis.com. "
            f"Details: {type(exc).__name__}: {exc}"
        )

    sheet_id = (env_values.get("GOOGLE_SHEET_ID") or os.getenv("GOOGLE_SHEET_ID") or "").strip()
    if sheet_id:
        try:
            sh = gc.open_by_key(sheet_id)
            _ok(f"Opened target sheet: {sh.title}")
        except Exception as exc:  # pragma: no cover - runtime validation script
            return _fail(
                "Could not open GOOGLE_SHEET_ID. Ensure the sheet is shared with the service account email as Viewer or higher. "
                f"Details: {type(exc).__name__}: {exc}"
            )
    else:
        print("[INFO] GOOGLE_SHEET_ID not set; skipped explicit sheet access test")

    print("\nChecklist runtime validation complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
