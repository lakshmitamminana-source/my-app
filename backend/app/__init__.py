"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat
from app.core.settings import settings
from app.db.session import engine
from app.models import Base


async def lifespan(app: FastAPI):
    """Lifespan context manager for app startup and shutdown."""
    # Startup: create tables (with graceful failure)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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
