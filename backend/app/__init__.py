"""FastAPI application factory."""
import json
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy import select

from app.api import auth, chat
from app.core.settings import settings
from app.db.session import async_session_maker, engine
from app.models import Base, Message


_LEGACY_ATTACHMENT_HEADER_RE = re.compile(
    r"\n\n### (?:(Image|Video): ([^\n]+)|(Table)|(Formula)|(Code))\n",
    re.MULTILINE,
)

_LEGACY_BRACKET_ATTACHMENT_RE = re.compile(
    r"\n\n\[(Image|Video|Table|Formula|Code)\](?: ([^\n]+))?\n?",
    re.MULTILINE,
)

_ASSISTANT_GENERATED_IMAGE_RE = re.compile(
    r"!\[Generated image\]\((data:image/[^)]+)\)",
    re.MULTILINE,
)


def _extract_legacy_attachments(content: str) -> tuple[str, list[dict]]:
    """Extract old inline attachment blocks from message content."""
    if not content:
        return content, []

    matches = list(_LEGACY_ATTACHMENT_HEADER_RE.finditer(content))
    is_bracket_mode = False
    if not matches:
        matches = list(_LEGACY_BRACKET_ATTACHMENT_RE.finditer(content))
        is_bracket_mode = True
    if not matches:
        fallback_marker = re.search(
            r"\n\n(?:\[(?:[A-Z]+ attachment:|Image\]|Video\]|Table\]|Formula\]|Code\])|### (?:Image:|Video:|Table|Formula|Code))",
            content,
        )
        if not fallback_marker:
            return content, []

        base_content = content[: fallback_marker.start()].rstrip()
        trailing_block = content[fallback_marker.start():].strip()
        if not trailing_block:
            return content, []

        return (
            base_content,
            [
                {
                    "type": "code",
                    "name": "legacy-attachment",
                    "mime_type": "text/plain",
                    "text_content": trailing_block,
                    "data_url": None,
                    "language": "text",
                }
            ],
        )

    attachments: list[dict] = []
    base_content = content[: matches[0].start()].rstrip()

    for idx, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[body_start:body_end].strip()

        if is_bracket_mode:
            bracket_type = (match.group(1) or "").strip()
            bracket_name = (match.group(2) or "attachment").strip()
            image_or_video_type = bracket_type if bracket_type in {"Image", "Video"} else None
            image_or_video_name = bracket_name
            is_table = bracket_type == "Table"
            is_formula = bracket_type == "Formula"
            is_code = bracket_type == "Code"
        else:
            image_or_video_type = match.group(1)
            image_or_video_name = (match.group(2) or "attachment").strip()
            is_table = bool(match.group(3))
            is_formula = bool(match.group(4))
            is_code = bool(match.group(5))

        if image_or_video_type == "Image":
            data_url_match = re.search(r"!\[[^\]]*\]\((data:[^)]+)\)", body)
            attachments.append(
                {
                    "type": "image",
                    "name": image_or_video_name,
                    "mime_type": None,
                    "text_content": None,
                    "data_url": data_url_match.group(1) if data_url_match else None,
                    "language": None,
                }
            )
            continue

        if image_or_video_type == "Video":
            data_url_match = re.search(r"\[Open video attachment\]\((data:[^)]+)\)", body)
            attachments.append(
                {
                    "type": "video",
                    "name": image_or_video_name,
                    "mime_type": None,
                    "text_content": None,
                    "data_url": data_url_match.group(1) if data_url_match else None,
                    "language": None,
                }
            )
            continue

        if is_table:
            attachments.append(
                {
                    "type": "table",
                    "name": "attachment",
                    "mime_type": None,
                    "text_content": body,
                    "data_url": None,
                    "language": None,
                }
            )
            continue

        if is_formula:
            formula_text = body
            if formula_text.startswith("$$") and formula_text.endswith("$$") and len(formula_text) >= 4:
                formula_text = formula_text[2:-2].strip()
            attachments.append(
                {
                    "type": "formula",
                    "name": "attachment",
                    "mime_type": None,
                    "text_content": formula_text,
                    "data_url": None,
                    "language": None,
                }
            )
            continue

        if is_code:
            code_match = re.match(r"^```([^\n]*)\n([\s\S]*?)\n```$", body)
            language = ""
            code_text = body
            if code_match:
                language = (code_match.group(1) or "").strip()
                code_text = code_match.group(2)

            attachments.append(
                {
                    "type": "code",
                    "name": "attachment",
                    "mime_type": None,
                    "text_content": code_text,
                    "data_url": None,
                    "language": language,
                }
            )

    return base_content, attachments


async def _backfill_legacy_message_attachments() -> None:
    """Migrate legacy inline attachment blocks into attachments_json."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Message).where(Message.attachments_json.is_(None))
        )
        messages = result.scalars().all()

        scanned = len(messages)
        mutated = 0
        detected = 0
        for message in messages:
            attachments = []
            cleaned_content = message.content

            if message.role == "user":
                cleaned_content, attachments = _extract_legacy_attachments(message.content)
            elif message.role == "assistant":
                image_match = _ASSISTANT_GENERATED_IMAGE_RE.search(message.content or "")
                if image_match:
                    attachments = [
                        {
                            "type": "image",
                            "name": "generated-image.png",
                            "mime_type": "image/png",
                            "text_content": None,
                            "data_url": image_match.group(1),
                            "language": None,
                        }
                    ]
                    cleaned_content = "Generated image:\n\nSee the generated image below."

            if not attachments:
                continue

            detected += 1

            message.attachments_json = json.dumps(attachments)
            if cleaned_content:
                message.content = cleaned_content
            mutated += 1

        if mutated:
            await session.commit()
            print(f"Backfilled attachments_json for {mutated} legacy messages")
        else:
            print(
                "Backfill scan complete: "
                f"scanned={scanned}, detected_legacy_blocks={detected}, migrated={mutated}"
            )


async def lifespan(app: FastAPI):
    """Lifespan context manager for app startup and shutdown."""
    # Startup: create tables (with graceful failure)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachments_json TEXT")
            )
        await _backfill_legacy_message_attachments()
    except Exception as e:
        # Log but don't fail startup if DB not available
        print(f"Warning: Could not initialize database: {e}")
    yield
    # Shutdown: close engine
    await engine.dispose()


# Create FastAPI app
app = FastAPI(
    title="Amzur AI Chat API",
    description="Multi-user chat platform with LiteLLM integration",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(chat.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
