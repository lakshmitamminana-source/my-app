"""LLM service for chat operations."""
import asyncio
import os
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.settings import settings


class LLMService:
    """Service for LLM operations."""

    def __init__(self):
        """Initialize LLM client."""
        self.llm = self._build_chat_model()

    def _build_chat_model(self) -> ChatOpenAI:
        """Build and configure ChatOpenAI model."""
        api_key = settings.LITELLM_API_KEY
        if not api_key:
            raise ValueError("LITELLM_API_KEY is not set. Add it to your .env file.")

        model_name = settings.LITELLM_CHAT_MODEL
        base_url = settings.LITELLM_PROXY_URL.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0.3,
            timeout=settings.LITELLM_TIMEOUT_SECONDS,
            max_retries=settings.LITELLM_MAX_RETRIES,
        )

    async def chat(
        self, messages_history: list[tuple[Literal["user", "assistant"], str]], current_message: str
    ) -> str:
        """Send a chat message and get response."""
        system_prompt = settings.SYSTEM_PROMPT
        messages = [SystemMessage(content=system_prompt)]

        # Add conversation history
        for role, content in messages_history[-20:]:
            if role == "user":
                messages.append(HumanMessage(content=content))
            else:
                messages.append(AIMessage(content=content))

        # Add current message
        messages.append(HumanMessage(content=current_message))

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self.llm.invoke, messages),
                timeout=settings.LITELLM_HARD_TIMEOUT_SECONDS,
            )
            answer_text = str(response.content)
            if not answer_text.strip():
                answer_text = "I received an empty response from the model."
            return answer_text
        except asyncio.TimeoutError:
            raise TimeoutError("Model request timed out")


# Singleton instance
_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Get or create LLM service singleton."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
