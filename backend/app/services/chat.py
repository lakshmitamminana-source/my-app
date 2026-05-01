"""Chat service for persisting conversations."""
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Message, Thread, User
from app.schemas import MessageResponse, ThreadResponse


class ChatService:
    """Service for chat operations."""

    @staticmethod
    def build_thread_title(prompt: str) -> str:
        """Build a concise, readable thread title from the first prompt."""
        normalized = " ".join(prompt.strip().split())
        normalized = re.sub(r"[`*_#>\[\]]", "", normalized)
        normalized = normalized.strip(" .,!?:;-")

        if not normalized:
            return "New Chat"

        prefixes = (
            "can you ",
            "could you ",
            "would you ",
            "please ",
            "help me ",
            "tell me ",
            "show me ",
            "i want to ",
            "how do i ",
            "how to ",
            "what is ",
            "what are ",
            "explain ",
        )

        lowered = normalized.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                normalized = normalized[len(prefix):].lstrip()
                break

        words = normalized.split()
        short_title = " ".join(words[:6])

        if len(short_title) > 50:
            short_title = short_title[:47].rstrip()

        if len(words) > 6 or len(normalized) > len(short_title):
            short_title = f"{short_title.rstrip('.')}..."

        return short_title[:1].upper() + short_title[1:] if short_title else "New Chat"

    async def create_thread(self, db: AsyncSession, user_id: uuid.UUID, title: str | None = None) -> Thread:
        """Create a new chat thread.
        
        Args:
            db: Database session
            user_id: User ID
            title: Optional thread title. If not provided, generates a default title.
        """
        if not title:
            title = "New Chat"
            
        thread = Thread(
            id=uuid.uuid4(),
            user_id=user_id,
            title=title,
        )
        db.add(thread)
        await db.commit()
        await db.refresh(thread)
        return thread

    async def get_user_threads(self, db: AsyncSession, user_id: uuid.UUID) -> list[Thread]:
        """Get all threads for a user."""
        stmt = (
            select(Thread)
            .where(Thread.user_id == user_id)
            .order_by(Thread.updated_at.desc())
            .options(selectinload(Thread.messages))
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_thread(self, db: AsyncSession, thread_id: uuid.UUID) -> Thread | None:
        """Get a single thread with messages."""
        stmt = select(Thread).where(Thread.id == thread_id).options(selectinload(Thread.messages))
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def save_message(
        self, db: AsyncSession, thread_id: uuid.UUID, role: str, content: str
    ) -> Message:
        """Save a message to a thread."""
        message = Message(
            id=uuid.uuid4(),
            thread_id=thread_id,
            role=role,
            content=content,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)

        # Update thread's updated_at timestamp
        await db.execute(
            select(Thread).where(Thread.id == thread_id)
        )
        thread = await db.execute(select(Thread).where(Thread.id == thread_id))
        thread = thread.scalar_one_or_none()
        if thread:
            thread.updated_at = message.created_at
            await db.commit()

        return message

    async def get_thread_messages(
        self, db: AsyncSession, thread_id: uuid.UUID
    ) -> list[Message]:
        """Get all messages in a thread."""
        stmt = (
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at.asc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_context_messages(
        self, db: AsyncSession, thread_id: uuid.UUID, limit: int = 5
    ) -> list[tuple[str, str]]:
        """Get the last N messages for conversation context.
        
        Args:
            db: Database session
            thread_id: Thread ID
            limit: Number of previous messages to retrieve
            
        Returns:
            List of (role, content) tuples
        """
        stmt = (
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()
        # Reverse to get chronological order
        messages.reverse()
        return [(msg.role, msg.content) for msg in messages]

    async def update_thread_title(
        self, db: AsyncSession, thread_id: uuid.UUID, title: str
    ) -> Thread | None:
        """Update thread title."""
        thread = await self.get_thread(db, thread_id)
        if thread:
            thread.title = title
            await db.commit()
            await db.refresh(thread)
        return thread

    async def delete_thread(self, db: AsyncSession, thread_id: uuid.UUID) -> bool:
        """Delete a thread and all its messages."""
        thread = await self.get_thread(db, thread_id)
        if thread:
            await db.delete(thread)
            await db.commit()
            return True
        return False
