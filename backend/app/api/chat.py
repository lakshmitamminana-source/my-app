"""Chat API routes."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.settings import settings
from app.db.session import get_db_session
from app.models import User
from app.schemas import ChatRequest, ChatResponse, MessageTurn, ThreadListResponse, ThreadResponse
from app.services.chat import ChatService
from app.services.llm import get_llm_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


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
        db, thread_id, "user", payload.message
    )

    try:
        # Get LLM response
        llm_service = get_llm_service()
        
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
        
        answer = await llm_service.chat(history, payload.message)

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
