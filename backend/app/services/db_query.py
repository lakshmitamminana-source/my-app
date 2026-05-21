"""Database query service for natural-language SQL questions."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI, OpenAIError
from sqlalchemy import create_engine, inspect, text

from app.core.settings import settings


@dataclass
class StoredDbConnection:
    """Server-side database connection metadata."""

    id: str
    user_id: str
    label: str
    database_url: str
    masked_url: str
    thread_id: str | None
    updated_at: datetime


class DatabaseQueryService:
    """Service that translates natural language questions into read-only SQL."""

    _BLOCKED_SQL_PATTERN = re.compile(
        r"\b(insert|update|delete|drop|truncate|alter)\b",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        base_url = settings.LITELLM_PROXY_URL.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        self._client = OpenAI(
            api_key=settings.LITELLM_API_KEY,
            base_url=base_url,
        )
        self._connections: dict[str, dict[str, StoredDbConnection]] = {}
        self._connections_lock = threading.Lock()

    @staticmethod
    def _mask_db_url(database_url: str) -> str:
        """Mask credentials in DB URL for safe display."""
        raw = (database_url or "").strip()
        if "@" not in raw:
            return raw
        prefix, suffix = raw.split("@", 1)
        if "://" not in prefix:
            return f"***@{suffix}"
        scheme, _ = prefix.split("://", 1)
        return f"{scheme}://***@{suffix}"

    def save_connection(
        self,
        *,
        user_id: uuid.UUID,
        label: str,
        database_url: str,
        thread_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        """Store a DB connection in server memory and return safe metadata."""
        normalized_url = self._build_sync_db_url(database_url)
        if not self._is_supported_db_url(normalized_url):
            raise ValueError(
                "Unsupported database URL. Use postgresql+psycopg2://, mysql+pymysql://, or sqlite:///"
            )

        connection = StoredDbConnection(
            id=uuid.uuid4().hex,
            user_id=str(user_id),
            label=label.strip(),
            database_url=normalized_url,
            masked_url=self._mask_db_url(normalized_url),
            thread_id=str(thread_id) if thread_id else None,
            updated_at=datetime.now(timezone.utc),
        )

        with self._connections_lock:
            user_connections = self._connections.setdefault(str(user_id), {})
            user_connections[connection.id] = connection

        return {
            "id": connection.id,
            "label": connection.label,
            "masked_url": connection.masked_url,
            "thread_id": uuid.UUID(connection.thread_id) if connection.thread_id else None,
            "updated_at": connection.updated_at,
        }

    def list_connections(self, *, user_id: uuid.UUID, thread_id: uuid.UUID | None) -> list[dict[str, Any]]:
        """List user's saved DB connections, optionally filtered by thread."""
        with self._connections_lock:
            user_connections = list(self._connections.get(str(user_id), {}).values())

        if thread_id:
            user_connections = [
                item for item in user_connections if item.thread_id in {None, str(thread_id)}
            ]

        user_connections.sort(key=lambda item: item.updated_at, reverse=True)
        return [
            {
                "id": item.id,
                "label": item.label,
                "masked_url": item.masked_url,
                "thread_id": uuid.UUID(item.thread_id) if item.thread_id else None,
                "updated_at": item.updated_at,
            }
            for item in user_connections
        ]

    def resolve_connection_url(self, *, user_id: uuid.UUID, connection_id: str) -> str:
        """Resolve and return stored DB URL for a user's connection id."""
        with self._connections_lock:
            user_connections = self._connections.get(str(user_id), {})
            connection = user_connections.get(connection_id)
        if not connection:
            raise ValueError("Connection not found. Save a connection first or provide database_url.")
        return connection.database_url

    @staticmethod
    def _build_sync_db_url(database_url: str) -> str:
        """Convert async Postgres URLs to sync driver URL for SQL execution."""
        normalized = (database_url or "").strip()

        if normalized.startswith("postgresql+asyncpg://"):
            return normalized.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        if normalized.startswith("postgresql+psycopg://"):
            return normalized.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
        if normalized.startswith("postgresql://"):
            return normalized.replace("postgresql://", "postgresql+psycopg2://", 1)
        return normalized

    @staticmethod
    def _is_supported_db_url(database_url: str) -> bool:
        url = (database_url or "").lower()
        return url.startswith((
            "postgresql+psycopg2://",
            "mysql+pymysql://",
            "sqlite:///",
        ))

    @staticmethod
    def _is_read_only_sql(sql_query: str) -> bool:
        sql = (sql_query or "").strip().lower()
        if not sql:
            return False

        allowed_prefixes = ("select", "with", "show", "describe", "desc", "explain", "pragma")
        return sql.startswith(allowed_prefixes)

    @classmethod
    def _validate_sql(cls, sql_query: str) -> None:
        if cls._BLOCKED_SQL_PATTERN.search(sql_query or ""):
            raise ValueError(
                "Only read-only queries are allowed. Blocked keywords: "
                "INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER."
            )
        if not cls._is_read_only_sql(sql_query):
            raise ValueError("Only read-only SQL statements are allowed.")

    @staticmethod
    def _extract_sql(raw_response: str) -> str:
        text_response = (raw_response or "").strip()
        code_block_match = re.search(r"```sql\s*([\s\S]*?)```", text_response, re.IGNORECASE)
        if code_block_match:
            return code_block_match.group(1).strip().rstrip(";")
        generic_block_match = re.search(r"```\s*([\s\S]*?)```", text_response)
        if generic_block_match:
            return generic_block_match.group(1).strip().rstrip(";")
        return text_response.rstrip(";")

    @staticmethod
    def _apply_limit(sql_query: str, max_rows: int = 200) -> str:
        """Add a LIMIT guardrail when no limit exists."""
        normalized = sql_query.strip().rstrip(";")
        if re.search(r"\blimit\s+\d+\b", normalized, re.IGNORECASE):
            return normalized

        if normalized.lower().startswith(("select", "with")):
            return f"{normalized}\nLIMIT {max_rows}"
        return normalized

    @staticmethod
    def _schema_summary(database_url: str) -> str:
        engine = create_engine(database_url, pool_pre_ping=True)
        try:
            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            if not table_names:
                return "No tables found."

            lines: list[str] = []
            for table_name in table_names[:40]:
                columns = inspector.get_columns(table_name)
                column_specs = [f"{col['name']} ({col.get('type')})" for col in columns[:25]]
                lines.append(f"- {table_name}: {', '.join(column_specs)}")
            return "\n".join(lines)
        finally:
            engine.dispose()

    def _generate_sql(self, question: str, schema_info: str, user_email: str) -> str:
        completion = self._client.chat.completions.create(
            model=settings.LITELLM_CHAT_MODEL,
            temperature=0.0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a SQL assistant. Convert natural language into exactly one read-only SQL query. "
                        "Never produce INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER. "
                        "Return SQL only, no explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Database schema:\n{schema_info}\n\n"
                        f"Question:\n{question}\n\n"
                        "Return only SQL."
                    ),
                },
            ],
            user=user_email,
            extra_body={
                "metadata": {
                    "application": settings.APP_NAME,
                    "environment": settings.ENVIRONMENT,
                }
            },
        )

        content = (completion.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("Could not generate SQL for this question.")
        return self._extract_sql(content)

    @staticmethod
    def _execute_sql(database_url: str, sql_query: str) -> tuple[list[str], list[dict[str, Any]]]:
        engine = create_engine(database_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                result = connection.execute(text(sql_query))
                rows = result.mappings().all()
                columns = list(result.keys())
                normalized_rows = [dict(row) for row in rows]
                return columns, normalized_rows
        finally:
            engine.dispose()

    def _summarize_results(
        self,
        *,
        question: str,
        sql_query: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        user_email: str,
    ) -> str:
        preview_rows = rows[:50]
        completion = self._client.chat.completions.create(
            model=settings.LITELLM_CHAT_MODEL,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You explain SQL query results to business users. "
                        "Be concise, accurate, and mention when no rows were returned."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n"
                        f"SQL: {sql_query}\n"
                        f"Columns: {json.dumps(columns)}\n"
                        f"Rows preview: {json.dumps(preview_rows, default=str)}\n"
                        f"Total rows returned: {len(rows)}"
                    ),
                },
            ],
            user=user_email,
            extra_body={
                "metadata": {
                    "application": settings.APP_NAME,
                    "environment": settings.ENVIRONMENT,
                }
            },
        )
        answer = (completion.choices[0].message.content or "").strip()
        if answer:
            return answer
        if not rows:
            return "No rows were returned for this question."
        return f"Returned {len(rows)} row(s)."

    async def query_database(
        self,
        *,
        database_url: str,
        question: str,
        user_email: str,
    ) -> dict[str, Any]:
        sync_db_url = self._build_sync_db_url(database_url)
        if not self._is_supported_db_url(sync_db_url):
            raise ValueError(
                "Unsupported database URL. Use postgresql+psycopg2://, mysql+pymysql://, or sqlite:///"
            )

        try:
            schema_info = await asyncio.to_thread(self._schema_summary, sync_db_url)
            raw_sql = await asyncio.to_thread(self._generate_sql, question, schema_info, user_email)
            safe_sql = self._apply_limit(raw_sql)
            self._validate_sql(safe_sql)
            columns, rows = await asyncio.to_thread(self._execute_sql, sync_db_url, safe_sql)
            answer = await asyncio.to_thread(
                self._summarize_results,
                question=question,
                sql_query=safe_sql,
                columns=columns,
                rows=rows,
                user_email=user_email,
            )
            return {
                "answer": answer,
                "sql": safe_sql,
                "columns": columns,
                "rows": rows[:200],
                "row_count": len(rows),
            }
        except OpenAIError as exc:
            raise RuntimeError(f"LLM proxy error: {exc}") from exc


_db_query_service: DatabaseQueryService | None = None


def get_db_query_service() -> DatabaseQueryService:
    """Get singleton DB query service."""
    global _db_query_service
    if _db_query_service is None:
        _db_query_service = DatabaseQueryService()
    return _db_query_service
