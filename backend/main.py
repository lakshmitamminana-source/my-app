"""Entry point for FastAPI application."""
import sys
from pathlib import Path

# Add backend directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app import app

__all__ = ["app"]
