"""Chat API routes."""
import uuid
import re
import logging
import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import settings
from app.db.session import get_db_session
from app.models import User
from app.schemas import (
    ChatAttachment,
    ChatRequest,
    ChatResponse,
    DataframeQueryRequest,
    DataframeQueryResponse,
    DbConnectionResponse,
    GoogleSheetLoadRequest,
    GoogleSheetLoadResponse,
    GoogleSheetQuestionRequest,
    GoogleSheetQuestionResponse,
    MessageTurn,
    QueryDbRequest,
    QueryDbResponse,
    ResearchDigestPdfExportRequest,
    ResearchDigestProgressEvent,
    ResearchDigestRequest,
    ResearchDigestResult,
    SaveDbConnectionRequest,
    ThreadListResponse,
    ThreadResponse,
)
from app.services.chat import ChatService
from app.services.dataframe_agent import get_dataframe_query_service
from app.services.db_query import get_db_query_service
from app.services.llm import get_llm_service
from app.services.rag import get_rag_service
from app.services.research_digest import get_research_digest_service
from app.services.sheets_service import ask_sheet_question, load_sheet_preview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


_LEAKED_ATTACHMENT_BLOCK_RE = re.compile(r"\n*Attached content:\n[\s\S]*$", re.IGNORECASE)
_ECHOED_IMAGE_GENERATION_RE = re.compile(
    r"^Generated image:\s*\n+See the generated image below\.?",
    re.IGNORECASE | re.MULTILINE,
)
_IMAGE_EDIT_REFUSAL_RE = re.compile(
    r"(cannot|can't|unable to)\s+(modify|edit)\s+(existing\s+)?images?",
    re.IGNORECASE,
)
_TEXT_ONLY_IMAGE_CLAIM_RE = re.compile(
    r"^(here\s+is\s+an?\s+image\b|generated\s+image\b|image\s+featuring\b)",
    re.IGNORECASE,
)


def _safe_error_message(exc: Exception, fallback: str) -> str:
    """Build a non-empty error message for API responses."""
    message = str(exc).strip()
    if message:
        return message
    return f"{fallback} ({exc.__class__.__name__})"


def _strip_leaked_attachment_context(text: str) -> str:
    """Remove internal attachment-context text from user-facing assistant content."""
    if not text:
        return text
    cleaned = _LEAKED_ATTACHMENT_BLOCK_RE.sub("", text).strip()
    # If the entire answer is just the echoed image template with no real content,
    # replace it with a neutral fallback so the user isn't misled.
    if _ECHOED_IMAGE_GENERATION_RE.match(cleaned) and len(cleaned) < 200:
        cleaned = "I wasn't able to generate an image for that request. Please try rephrasing your prompt."
    return cleaned


@router.post("/tools/query-db", response_model=QueryDbResponse)
async def query_database_tool(
    payload: QueryDbRequest,
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> QueryDbResponse:
    """Run a natural-language database question through a read-only SQL pipeline."""
    db_query_service = get_db_query_service()
    try:
        if payload.connection_id:
            database_url = db_query_service.resolve_connection_url(
                user_id=current_user.id,
                connection_id=payload.connection_id,
            )
        elif payload.database_url:
            database_url = payload.database_url
        else:
            raise ValueError("Provide either connection_id or database_url.")

        result = await db_query_service.query_database(
            database_url=database_url,
            question=payload.question,
            user_email=current_user.email,
        )
        return QueryDbResponse.model_validate(result)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_query", "message": str(exc)},
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "llm_error", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "unexpected", "message": str(exc)},
        )


@router.post("/tools/db-connections", response_model=DbConnectionResponse)
async def save_db_connection_tool(
    payload: SaveDbConnectionRequest,
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> DbConnectionResponse:
    """Save a database connection in a server-side vault for safer reuse."""
    db_query_service = get_db_query_service()
    try:
        saved = db_query_service.save_connection(
            user_id=current_user.id,
            label=payload.label,
            database_url=payload.database_url,
            thread_id=payload.thread_id,
        )
        return DbConnectionResponse.model_validate(saved)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_connection", "message": str(exc)},
        )


@router.get("/tools/db-connections", response_model=list[DbConnectionResponse])
async def list_db_connections_tool(
    thread_id: uuid.UUID | None = Query(default=None),
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> list[DbConnectionResponse]:
    """List saved database connections for the current user."""
    db_query_service = get_db_query_service()
    items = db_query_service.list_connections(user_id=current_user.id, thread_id=thread_id)
    return [DbConnectionResponse.model_validate(item) for item in items]


@router.post("/tools/google-sheets/load", response_model=GoogleSheetLoadResponse)
async def load_google_sheet_tool(
    payload: GoogleSheetLoadRequest,
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> GoogleSheetLoadResponse:
    """Load Google Sheet data into a preview table for the tool panel."""
    try:
        preview = load_sheet_preview(payload.sheet_url_or_id)
        return GoogleSheetLoadResponse.model_validate(preview)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_sheet", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "sheet_load_error",
                "message": _safe_error_message(exc, "Failed to load Google Sheet"),
            },
        )


@router.post("/tools/google-sheets/ask", response_model=GoogleSheetQuestionResponse)
async def ask_google_sheet_tool(
    payload: GoogleSheetQuestionRequest,
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> GoogleSheetQuestionResponse:
    """Ask a natural-language question over Google Sheet data."""
    try:
        result = ask_sheet_question(
            sheet_url_or_id=payload.sheet_url_or_id,
            question=payload.question,
            user_email=current_user.email,
        )
        return GoogleSheetQuestionResponse.model_validate(result)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_sheet", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "sheet_question_error",
                "message": _safe_error_message(exc, "Failed to answer Google Sheet question"),
            },
        )


@router.post("/tools/upload-local-file")
async def upload_local_file_tool(
    file: Annotated[UploadFile, File()],
    current_user: Annotated[User, Depends(get_current_user)] = None,
):
    """Upload a CSV or XLSX file for use with the dataframe query tool."""
    import shutil
    from pathlib import Path

    allowed_types = set(settings.ALLOWED_DATAFRAME_MIME_TYPES)
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_file_type",
                "message": f"Only CSV and Excel files are accepted. Got: {file.content_type}",
            },
        )

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "file_too_large",
                "message": f"File exceeds the {settings.MAX_UPLOAD_MB} MB limit.",
            },
        )

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w.\-]", "_", Path(file.filename or "upload").name)
    dest = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
    dest.write_bytes(content)

    # Quick preview so the frontend can show column names and row count
    try:
        import pandas as pd

        if file.content_type == "text/csv":
            df = pd.read_csv(dest)
            source_type = "csv"
        else:
            df = pd.read_excel(dest)
            source_type = "xlsx"

        preview_df = df.head(20).fillna("")
        return {
            "path": str(dest),
            "filename": safe_name,
            "source_type": source_type,
            "row_count": int(len(df.index)),
            "columns": [str(c) for c in df.columns.tolist()],
            "rows": preview_df.astype(str).to_dict(orient="records"),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "file_parse_error",
                "message": _safe_error_message(exc, "Could not parse uploaded file"),
            },
        )


@router.post("/tools/dataframe-query", response_model=DataframeQueryResponse)
async def dataframe_query_tool(
    payload: DataframeQueryRequest,
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> DataframeQueryResponse:
    """Query CSV/XLSX/Google Sheet dataframes using natural language."""
    service = get_dataframe_query_service()
    try:
        result = service.query(
            source_type=payload.source_type,
            source=payload.source,
            question=payload.question,
            user_email=current_user.email,
            worksheet_name=payload.worksheet_name,
            worksheet_index=payload.worksheet_index,
            history=payload.history,
            preview_rows=payload.preview_rows,
            include_table=payload.include_table,
        )
        return DataframeQueryResponse.model_validate(result)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_dataframe_query", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "dataframe_query_error",
                "message": _safe_error_message(exc, "Failed to query dataframe"),
            },
        )


@router.get("/tools/research-digest/stream")
async def research_digest_stream(
    topic: str = Query(..., min_length=3, max_length=300),
    max_iterations: int = Query(default=3, ge=1, le=8),
    papers_per_iteration: int = Query(default=4, ge=1, le=10),
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> StreamingResponse:
    """Stream autonomous research digest progress and final result via SSE."""
    payload = ResearchDigestRequest(
        topic=topic,
        max_iterations=max_iterations,
        papers_per_iteration=papers_per_iteration,
    )
    service = get_research_digest_service()

    async def event_generator():
        queue: asyncio.Queue[ResearchDigestProgressEvent] = asyncio.Queue()

        async def on_progress(event: ResearchDigestProgressEvent) -> None:
            await queue.put(event)

        task = asyncio.create_task(
            service.run_arxiv_agent(
                topic=payload.topic,
                user_email=current_user.email,
                max_iterations=payload.max_iterations,
                papers_per_iteration=payload.papers_per_iteration,
                progress_callback=on_progress,
            )
        )

        try:
            while True:
                if task.done() and queue.empty():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.6)
                    yield f"event: progress\ndata: {event.model_dump_json()}\n\n"
                except TimeoutError:
                    yield "event: ping\ndata: {}\n\n"

            digest = await task
            result = ResearchDigestResult.model_validate(digest)
            yield f"event: complete\ndata: {result.model_dump_json()}\n\n"
        except Exception as exc:
            error_event = ResearchDigestProgressEvent(
                phase="error",
                message=_safe_error_message(exc, "Research digest failed"),
                progress=1.0,
            )
            yield f"event: error\ndata: {error_event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tools/research-digest/export-pdf")
async def export_research_digest_pdf(
    payload: ResearchDigestPdfExportRequest,
    current_user: Annotated[User, Depends(get_current_user)] = None,
) -> Response:
    """Export a generated research digest as a PDF document."""
    try:
        from fpdf import FPDF
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "pdf_export_dependency_missing",
                "message": _safe_error_message(exc, "PDF export dependency is unavailable"),
            },
        )

    def pdf_safe_text(value: str | None) -> str:
        """Convert text to a core-font-safe latin-1 string for FPDF output."""
        text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
        return text.encode("latin-1", errors="replace").decode("latin-1")

    def break_long_tokens(value: str, token_size: int = 36) -> str:
        """Insert spaces into long tokens so FPDF can wrap without layout errors."""
        if not value:
            return ""

        def split_token(token: str) -> str:
            if len(token) <= token_size:
                return token
            chunks = [token[i:i + token_size] for i in range(0, len(token), token_size)]
            return " ".join(chunks)

        lines = value.split("\n")
        out_lines: list[str] = []
        for line in lines:
            tokens = line.split(" ")
            out_lines.append(" ".join(split_token(token) for token in tokens))
        return "\n".join(out_lines)

    def write_wrapped_text(pdf_doc, text: str, line_height: float = 6.0) -> None:
        """Write text with strict width-aware wrapping to avoid FPDF layout overflows."""
        max_width = pdf_doc.w - pdf_doc.l_margin - pdf_doc.r_margin
        if max_width < 20:
            # Defensive fallback if margins were changed unexpectedly.
            pdf_doc.set_left_margin(10)
            pdf_doc.set_right_margin(10)
            max_width = pdf_doc.w - pdf_doc.l_margin - pdf_doc.r_margin
        content = break_long_tokens(pdf_safe_text(text), token_size=24)

        for paragraph in content.split("\n"):
            paragraph = paragraph.replace("\t", " ")
            if not paragraph:
                pdf_doc.ln(line_height)
                continue

            line = ""
            for char in paragraph:
                trial = line + char
                if pdf_doc.get_string_width(trial) <= max_width:
                    line = trial
                else:
                    if line:
                        pdf_doc.multi_cell(
                            0,
                            line_height,
                            line.rstrip(),
                            new_x="LMARGIN",
                            new_y="NEXT",
                        )
                        line = char
                    else:
                        # Extremely wide single glyph fallback.
                        pdf_doc.multi_cell(
                            0,
                            line_height,
                            "?",
                            new_x="LMARGIN",
                            new_y="NEXT",
                        )
                        line = ""
            if line:
                pdf_doc.multi_cell(
                    0,
                    line_height,
                    line.rstrip(),
                    new_x="LMARGIN",
                    new_y="NEXT",
                )

    try:
        digest = payload.digest
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 16)
        write_wrapped_text(pdf, f"Research Digest: {digest.topic}", line_height=8)
        pdf.ln(2)

        def section(title: str, body: str):
            pdf.set_font("Helvetica", "B", 12)
            write_wrapped_text(pdf, title, line_height=7)
            pdf.set_font("Helvetica", "", 11)
            write_wrapped_text(pdf, body, line_height=6)
            pdf.ln(1)

        section("Executive Summary", digest.executive_summary)
        section("Evidence Assessment", digest.evidence_assessment)
        section("Methodology", digest.methodology_notes)

        if digest.key_findings:
            section("Key Findings", "\n".join(f"- {item}" for item in digest.key_findings))
        if digest.limitations:
            section("Limitations", "\n".join(f"- {item}" for item in digest.limitations))
        if digest.next_questions:
            section("Next Questions", "\n".join(f"- {item}" for item in digest.next_questions))

        if digest.citations:
            pdf.set_font("Helvetica", "B", 12)
            write_wrapped_text(pdf, "Citations", line_height=7)
            pdf.set_font("Helvetica", "", 10)
            for idx, citation in enumerate(digest.citations, start=1):
                line = (
                    f"[{idx}] {citation.title} | {', '.join(citation.authors[:3])} | "
                    f"{citation.published} | {citation.arxiv_id}"
                )
                write_wrapped_text(pdf, line, line_height=5)
            pdf.ln(1)

        raw_pdf = pdf.output(dest="S")
        if isinstance(raw_pdf, (bytes, bytearray)):
            pdf_bytes = bytes(raw_pdf)
        else:
            pdf_bytes = str(raw_pdf).encode("latin-1", errors="replace")

        filename = f"research_digest_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "pdf_export_failed",
                "message": _safe_error_message(exc, "Failed to export digest PDF"),
            },
        )


@router.post("/extract-pdf")
async def extract_pdf_text(
    file: Annotated[UploadFile, File()],
    current_user: Annotated[User, Depends(get_current_user)] = None,
):
    """Extract text from an uploaded PDF file."""
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF",
        )

    try:
        from pypdf import PdfReader
        import io

        # Read PDF file from upload
        content = await file.read()
        pdf_file = io.BytesIO(content)
        
        # Extract text
        reader = PdfReader(pdf_file)
        text_content = ""
        
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
        
        return {
            "text": text_content.strip(),
            "pages": len(reader.pages),
            "filename": file.filename,
        }
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF extraction library not available",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to extract PDF: {str(e)}",
        )


@router.post("/threads", response_model=ThreadListResponse)
async def create_thread(
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> ThreadListResponse:
    """Create a new chat thread."""
    chat_service = ChatService()
    thread = await chat_service.create_thread(db, current_user.id, "New Chat")
    return ThreadListResponse.model_validate(thread)


@router.get("/threads", response_model=list[ThreadListResponse])
async def get_threads(
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> list[ThreadListResponse]:
    """Get all threads for current user."""
    chat_service = ChatService()
    threads = await chat_service.get_user_threads(db, current_user.id)
    return [ThreadListResponse.model_validate(thread) for thread in threads]


@router.get("/threads/{thread_id}", response_model=ThreadResponse)
async def get_thread(
    thread_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> ThreadResponse:
    """Get a single thread with all messages."""
    chat_service = ChatService()
    thread = await chat_service.get_thread(db, thread_id)

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )

    if thread.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Sanitize any older assistant messages that may include leaked internal
    # attachment context text.
    for message in thread.messages:
        if message.role == "assistant":
            message.content = _strip_leaked_attachment_context(message.content)

    return ThreadResponse.model_validate(thread)


@router.post("/threads/{thread_id}/messages", response_model=ChatResponse)
async def send_message(
    thread_id: uuid.UUID,
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> ChatResponse:
    """Send a message and get LLM response with conversation memory."""
    chat_service = ChatService()
    
    # Verify thread ownership
    thread = await chat_service.get_thread(db, thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )

    if thread.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    should_update_title = thread.title == "New Chat" and len(thread.messages) == 0
    next_thread_title = None

    # Save user message
    user_message = await chat_service.save_message(
        db,
        thread_id,
        "user",
        payload.message,
        attachments=payload.attachments,
    )

    try:
        rag_service = get_rag_service()
        pdf_attachments = [
            attachment
            for attachment in payload.attachments
            if (attachment.type == "pdf" or (attachment.language or "").lower() == "pdf")
            and (attachment.text_content or "").strip()
        ]

        indexed_chunks = 0
        for attachment in pdf_attachments:
            indexed_chunks += await rag_service.ingest_pdf_text(
                user_id=current_user.id,
                thread_id=thread_id,
                message_id=user_message.id,
                source_name=attachment.name or "uploaded.pdf",
                text=attachment.text_content or "",
                user_email=current_user.email,
            )

        normalized_message = payload.message.strip().lower()
        if pdf_attachments and normalized_message in {"shared attachments", "shared attachment", "uploaded file", "upload"}:
            index_answer = (
                f"Indexed {len(pdf_attachments)} PDF file(s) into knowledge store "
                f"({indexed_chunks} chunks). You can now ask questions about the uploaded document."
            )
            assistant_message = await chat_service.save_message(
                db,
                thread_id,
                "assistant",
                index_answer,
            )

            if should_update_title:
                next_thread_title = chat_service.build_thread_title(payload.message)
                await chat_service.update_thread_title(db, thread_id, next_thread_title)

            return ChatResponse(
                answer=index_answer,
                message_id=assistant_message.id,
                thread_title=next_thread_title,
            )

        # Get LLM response
        logger.info(f"send_message: thread_id={thread_id} message='{payload.message[:80]}...'")
        llm_service = get_llm_service()

        latest_assistant_image_url = await chat_service.get_latest_assistant_image_url(db, thread_id)
        logger.info(f"latest_assistant_image_url: {bool(latest_assistant_image_url)}")
        
        is_followup_image_edit = (
            latest_assistant_image_url is not None
            and llm_service.is_followup_image_edit_request(payload.message)
        )
        logger.info(f"is_followup_image_edit: {is_followup_image_edit}")

        if llm_service.is_image_generation_request(payload.message) or is_followup_image_edit:
            logger.info(f"Image generation/edit path taken")
            request_image_urls = [
                attachment.data_url
                for attachment in payload.attachments
                if attachment.type == "image" and attachment.data_url
            ]
            reference_image_url = request_image_urls[0] if request_image_urls else latest_assistant_image_url

            image_url, _ = await llm_service.generate_image(
                payload.message,
                current_user.email,
                reference_image_data_url=reference_image_url,
            )
            logger.info(f"Image generated successfully, length={len(image_url)}")

            assistant_attachments = [
                ChatAttachment(
                    type="image",
                    name="generated-image.png",
                    mime_type="image/png",
                    data_url=image_url,
                )
            ]

            image_answer = (
                "Generated image:\n\n"
                "See the generated image below."
            )

            assistant_message = await chat_service.save_message(
                db,
                thread_id,
                "assistant",
                image_answer,
                attachments=assistant_attachments,
            )

            if should_update_title:
                next_thread_title = chat_service.build_thread_title(payload.message)
                await chat_service.update_thread_title(db, thread_id, next_thread_title)

            return ChatResponse(
                answer=image_answer,
                assistant_attachments=assistant_attachments,
                message_id=assistant_message.id,
                thread_title=next_thread_title,
            )
        
        # Build conversation history with memory window
        # Fetch the last N messages from the thread for context
        context_messages = await chat_service.get_context_messages(
            db, thread_id, limit=settings.CONVERSATION_MEMORY_WINDOW
        )
        
        # Use context messages from database, plus any client history
        # Database messages take precedence for consistency
        if context_messages:
            history = context_messages
        else:
            history = [(turn.role, turn.content) for turn in payload.history]
        
        prompt_message = chat_service.build_llm_prompt_message(
            payload.message,
            payload.attachments,
        )

        has_rag_docs = await rag_service.has_thread_documents(current_user.id, thread_id)
        if has_rag_docs:
            retrieved_chunks = await rag_service.retrieve_chunks(
                user_id=current_user.id,
                thread_id=thread_id,
                query=payload.message,
                user_email=current_user.email,
                top_k=settings.RAG_TOP_K,
            )

            if retrieved_chunks:
                answer = await rag_service.answer_from_chunks(
                    question=payload.message,
                    chunks=retrieved_chunks,
                    user_email=current_user.email,
                )

                assistant_message = await chat_service.save_message(
                    db, thread_id, "assistant", answer
                )

                if should_update_title:
                    next_thread_title = chat_service.build_thread_title(payload.message)
                    await chat_service.update_thread_title(db, thread_id, next_thread_title)

                return ChatResponse(
                    answer=answer,
                    message_id=assistant_message.id,
                    thread_title=next_thread_title,
                )
        
        # Collect image URLs from current request AND previous messages
        image_data_urls = [
            attachment.data_url
            for attachment in payload.attachments
            if attachment.type == "image" and attachment.data_url
        ]
        
        # Extract images from stored user attachments in thread history
        image_data_urls.extend(await chat_service.get_thread_image_urls(db, thread_id))
        
        # Remove duplicates while preserving order
        seen = set()
        image_data_urls = [
            url for url in image_data_urls
            if not (url in seen or seen.add(url))
        ]
        
        answer = await llm_service.chat(
            history,
            prompt_message,
            image_data_urls=image_data_urls,
            user_email=current_user.email,
        )
        answer = _strip_leaked_attachment_context(answer)

        # Safety fallback: if chat model refuses image editing as text,
        # automatically switch to image generation when a reference image exists.
        is_text_only_image_claim = bool(_TEXT_ONLY_IMAGE_CLAIM_RE.search(answer))
        if _IMAGE_EDIT_REFUSAL_RE.search(answer) or is_text_only_image_claim:
            latest_assistant_image_url = latest_assistant_image_url or await chat_service.get_latest_assistant_image_url(db, thread_id)
            if latest_assistant_image_url:
                request_image_urls = [
                    attachment.data_url
                    for attachment in payload.attachments
                    if attachment.type == "image" and attachment.data_url
                ]
                reference_image_url = request_image_urls[0] if request_image_urls else latest_assistant_image_url

                image_url, _ = await llm_service.generate_image(
                    payload.message,
                    current_user.email,
                    reference_image_data_url=reference_image_url,
                )

                assistant_attachments = [
                    ChatAttachment(
                        type="image",
                        name="generated-image.png",
                        mime_type="image/png",
                        data_url=image_url,
                    )
                ]

                image_answer = (
                    "Generated image:\n\n"
                    "See the generated image below."
                )

                assistant_message = await chat_service.save_message(
                    db,
                    thread_id,
                    "assistant",
                    image_answer,
                    attachments=assistant_attachments,
                )

                if should_update_title:
                    next_thread_title = chat_service.build_thread_title(payload.message)
                    await chat_service.update_thread_title(db, thread_id, next_thread_title)

                return ChatResponse(
                    answer=image_answer,
                    assistant_attachments=assistant_attachments,
                    message_id=assistant_message.id,
                    thread_title=next_thread_title,
                )

        # Save assistant message
        assistant_message = await chat_service.save_message(
            db, thread_id, "assistant", answer
        )

        if should_update_title:
            next_thread_title = chat_service.build_thread_title(payload.message)
            await chat_service.update_thread_title(db, thread_id, next_thread_title)

        return ChatResponse(
            answer=answer,
            message_id=assistant_message.id,
            thread_title=next_thread_title,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Model request timed out",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM error: {str(e)}",
        )


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> None:
    """Delete a thread."""
    chat_service = ChatService()
    thread = await chat_service.get_thread(db, thread_id)

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )

    if thread.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await chat_service.delete_thread(db, thread_id)


@router.put("/threads/{thread_id}", response_model=ThreadListResponse)
async def update_thread(
    thread_id: uuid.UUID,
    payload: dict,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> ThreadListResponse:
    """Update thread (e.g., rename title).
    
    Request body: {"title": "new_title"}
    """
    chat_service = ChatService()
    thread = await chat_service.get_thread(db, thread_id)

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found",
        )

    if thread.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if "title" in payload:
        updated_thread = await chat_service.update_thread_title(db, thread_id, payload["title"])
        return ThreadListResponse.model_validate(updated_thread)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No fields to update provided",
    )
