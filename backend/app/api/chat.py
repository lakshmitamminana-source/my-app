"""Chat API routes."""
import uuid
import re
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import settings
from app.db.session import get_db_session
from app.models import User
from app.schemas import ChatAttachment, ChatRequest, ChatResponse, MessageTurn, ThreadListResponse, ThreadResponse
from app.services.chat import ChatService
from app.services.llm import get_llm_service
from app.services.rag import get_rag_service

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
