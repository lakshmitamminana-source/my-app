"""Chat service for persisting conversations."""
import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Message, Thread, User
from app.schemas import ChatAttachment, MessageResponse, ThreadResponse


class ChatService:
    """Service for chat operations."""

    @staticmethod
    def _attachment_to_storage_dict(attachment: ChatAttachment) -> dict:
        """Normalize attachment into a JSON-serializable dict for DB storage."""
        return {
            "type": attachment.type,
            "name": attachment.name,
            "mime_type": attachment.mime_type,
            "text_content": attachment.text_content,
            "data_url": attachment.data_url,
            "language": attachment.language,
        }

    @staticmethod
    def _parse_stored_attachments(raw_attachments_json: str | None) -> list[dict]:
        """Parse stored attachments JSON into a list."""
        if not raw_attachments_json:
            return []
        try:
            parsed = json.loads(raw_attachments_json)
            if isinstance(parsed, list):
                return parsed
            return []
        except (json.JSONDecodeError, TypeError):
            return []

    def _format_stored_attachment_for_prompt(self, attachment: dict) -> str:
        """Format a stored attachment dict into concise LLM-readable text."""
        return self._format_attachment_for_prompt(
            ChatAttachment(
                type=attachment.get("type", "code"),
                name=attachment.get("name"),
                mime_type=attachment.get("mime_type"),
                text_content=attachment.get("text_content"),
                data_url=attachment.get("data_url"),
                language=attachment.get("language"),
            )
        )

    @staticmethod
    def _format_attachment_for_prompt(attachment: ChatAttachment) -> str:
        """Format an attachment into concise LLM-readable text."""
        attachment_name = attachment.name or "attachment"
        attachment_mime = attachment.mime_type or "unknown"

        # Ensure text_content is always a string
        def ensure_string(value):
            if isinstance(value, str):
                return value
            if value is None:
                return ""
            try:
                return str(value)
            except:
                return ""

        if attachment.type in {"image", "video"}:
            return f"[{attachment.type.upper()}] name={attachment_name}, mime_type={attachment_mime}"

        if attachment.type == "pdf":
            return f"[PDF] name={attachment_name}, mime_type={attachment_mime} (indexed for retrieval)"

        if attachment.type == "table":
            table_text = ensure_string(attachment.text_content).strip()
            return f"[TABLE]\n{table_text}"

        if attachment.type == "formula":
            formula_text = ensure_string(attachment.text_content).strip()
            return f"[FORMULA]\n{formula_text}"

        if attachment.type == "code":
            language = ensure_string(attachment.language).strip() or "text"
            code_text = ensure_string(attachment.text_content).strip()
            return f"[CODE language={language}]\n{code_text}"

        return ""

    def build_llm_prompt_message(
        self,
        message: str,
        attachments: list[ChatAttachment],
    ) -> str:
        """Build LLM message with normalized attachment context."""
        if not attachments:
            return message

        attachment_context = "\n\n".join(
            filter(None, (self._format_attachment_for_prompt(attachment) for attachment in attachments))
        )
        if not attachment_context:
            return message

        return (
            f"{message}\n\nAttached content:\n"
            f"{attachment_context}\n\n"
            "Use attached content as part of your answer when relevant."
        )

    @staticmethod
    def extract_image_urls_from_attachments(attachments: list[dict]) -> list[str]:
        """Extract image data URLs from stored attachments."""
        urls = []
        for attachment in attachments:
            if attachment.get("type") == "image" and attachment.get("data_url"):
                urls.append(attachment["data_url"])
        return urls

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
        self,
        db: AsyncSession,
        thread_id: uuid.UUID,
        role: str,
        content: str,
        attachments: list[ChatAttachment] | None = None,
    ) -> Message:
        """Save a message to a thread."""
        attachments_json = None
        if attachments:
            serialized_attachments = [
                self._attachment_to_storage_dict(attachment) for attachment in attachments
            ]
            attachments_json = json.dumps(serialized_attachments)

        message = Message(
            id=uuid.uuid4(),
            thread_id=thread_id,
            role=role,
            content=content,
            attachments_json=attachments_json,
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
        context_messages: list[tuple[str, str]] = []
        for msg in messages:
            stored_attachments = self._parse_stored_attachments(msg.attachments_json)
            attachment_context = "\n\n".join(
                filter(
                    None,
                    (self._format_stored_attachment_for_prompt(item) for item in stored_attachments),
                )
            )
            # Only include attachment context for user messages; including it in
            # assistant turns can leak implementation text back to users.
            if msg.role == "user" and attachment_context:
                context_messages.append((msg.role, f"{msg.content}\n\nAttached content:\n{attachment_context}"))
            else:
                context_messages.append((msg.role, msg.content))
        return context_messages

    async def get_thread_image_urls(self, db: AsyncSession, thread_id: uuid.UUID) -> list[str]:
        """Get all image data URLs from stored user attachments in a thread."""
        stmt = (
            select(Message)
            .where(Message.thread_id == thread_id)
            .where(Message.role == "user")
            .order_by(Message.created_at.asc())
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()

        image_urls: list[str] = []
        for msg in messages:
            stored_attachments = self._parse_stored_attachments(msg.attachments_json)
            image_urls.extend(self.extract_image_urls_from_attachments(stored_attachments))

        return image_urls

    async def get_latest_assistant_image_url(self, db: AsyncSession, thread_id: uuid.UUID) -> str | None:
        """Get the most recent assistant-generated image data URL from a thread."""
        stmt = (
            select(Message)
            .where(Message.thread_id == thread_id)
            .where(Message.role == "assistant")
            .order_by(Message.created_at.desc())
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()

        for msg in messages:
            stored_attachments = self._parse_stored_attachments(msg.attachments_json)
            for url in self.extract_image_urls_from_attachments(stored_attachments):
                if isinstance(url, str) and url.strip():
                    return url
        return None

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
