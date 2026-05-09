"""RAG service for PDF ingestion and grounded QA."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI

from app.core.settings import settings


@dataclass
class RetrievedChunk:
    """Single retrieved text chunk with source metadata."""

    chunk_id: str
    text: str
    source_name: str
    chunk_index: int
    similarity_score: float


class RagService:
    """Ingest and retrieve PDF knowledge using ChromaDB and embeddings."""

    def __init__(self) -> None:
        base_url = settings.LITELLM_PROXY_URL.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        self._openai_client = OpenAI(
            api_key=settings.LITELLM_API_KEY,
            base_url=base_url,
        )
        self._chat_llm = ChatOpenAI(
            model=settings.LITELLM_CHAT_MODEL,
            api_key=settings.LITELLM_API_KEY,
            base_url=base_url,
            temperature=0.1,
            timeout=settings.LITELLM_TIMEOUT_SECONDS,
            max_retries=settings.LITELLM_MAX_RETRIES,
        )
        self._chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)

    @staticmethod
    def _collection_name_for_user(user_id: uuid.UUID) -> str:
        return f"user_{str(user_id).replace('-', '_')}"

    def _get_or_create_collection(self, user_id: uuid.UUID) -> Collection:
        return self._chroma_client.get_or_create_collection(
            name=self._collection_name_for_user(user_id),
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
        """Split text into overlapping chunks by character size."""
        normalized = " ".join((text or "").split())
        if not normalized:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + chunk_size)
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end == len(normalized):
                break
            start = max(0, end - overlap)
        return chunks

    async def _embed_texts(self, texts: list[str], user_email: str) -> list[list[float]]:
        """Generate embeddings in batches via LiteLLM proxy."""
        if not texts:
            return []

        batch_size = 64
        vectors: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]

            def _request_batch() -> Any:
                return self._openai_client.embeddings.create(
                    model=settings.LITELLM_EMBEDDING_MODEL,
                    input=batch,
                    user=user_email,
                    extra_body={
                        "metadata": {
                            "application": settings.APP_NAME,
                            "environment": settings.ENVIRONMENT,
                        }
                    },
                )

            response = await asyncio.wait_for(
                asyncio.to_thread(_request_batch),
                timeout=settings.LITELLM_HARD_TIMEOUT_SECONDS,
            )
            vectors.extend(item.embedding for item in response.data)

        return vectors

    async def ingest_pdf_text(
        self,
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        message_id: uuid.UUID,
        source_name: str,
        text: str,
        user_email: str,
    ) -> int:
        """Chunk, embed, and store one PDF into Chroma for a user/thread."""
        chunks = self._chunk_text(text)
        if not chunks:
            return 0

        vectors = await self._embed_texts(chunks, user_email)
        collection = self._get_or_create_collection(user_id)

        ids: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            digest = hashlib.sha1(f"{thread_id}:{message_id}:{idx}:{chunk[:60]}".encode("utf-8")).hexdigest()
            ids.append(f"pdf_{digest}")
            metadatas.append(
                {
                    "thread_id": str(thread_id),
                    "message_id": str(message_id),
                    "source_name": source_name or "uploaded.pdf",
                    "chunk_index": idx,
                    "doc_type": "pdf",
                }
            )

        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            documents=chunks,
            embeddings=vectors,
            metadatas=metadatas,
        )
        return len(chunks)

    async def has_thread_documents(self, user_id: uuid.UUID, thread_id: uuid.UUID) -> bool:
        """Check whether this thread has indexed RAG documents."""
        collection = self._get_or_create_collection(user_id)
        result = await asyncio.to_thread(
            collection.get,
            where={"thread_id": str(thread_id)},
            limit=1,
            include=["metadatas"],
        )
        return bool(result and result.get("ids"))

    async def retrieve_chunks(
        self,
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        query: str,
        user_email: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Retrieve top-k relevant chunks for a question from thread-scoped docs."""
        collection = self._get_or_create_collection(user_id)
        query_embedding = (await self._embed_texts([query], user_email))[0]

        result = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=top_k,
            where={"thread_id": str(thread_id)},
            include=["documents", "metadatas", "distances"],
        )

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        ids = (result.get("ids") or [[]])[0]

        chunks: list[RetrievedChunk] = []
        for idx, text in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            distance = float(distances[idx]) if idx < len(distances) else 1.0
            source_name = str(metadata.get("source_name") or "uploaded.pdf")
            chunk_index = int(metadata.get("chunk_index") or 0)
            chunk_id = ids[idx] if idx < len(ids) else f"chunk_{idx}"

            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    text=str(text),
                    source_name=source_name,
                    chunk_index=chunk_index,
                    similarity_score=max(0.0, 1.0 - distance),
                )
            )
        return chunks

    async def answer_from_chunks(
        self,
        *,
        question: str,
        chunks: list[RetrievedChunk],
        user_email: str,
    ) -> str:
        """Answer using only retrieved chunks and append explicit source references."""
        if not chunks:
            return "I could not find relevant content in the uploaded PDF for this question."

        context_blocks = []
        for i, chunk in enumerate(chunks, start=1):
            context_blocks.append(
                (
                    f"[SOURCE {i}] file={chunk.source_name} chunk={chunk.chunk_index}\n"
                    f"{chunk.text}"
                )
            )

        system_prompt = (
            "You are a document QA assistant. Answer ONLY using the provided source chunks. "
            "If the answer is not present, say you cannot find it in the uploaded PDF. "
            "Do not use outside knowledge. Keep the answer concise and factual. "
            "When stating facts, cite source numbers like [1], [2]."
        )
        human_prompt = (
            f"Question: {question}\n\n"
            "Sources:\n"
            f"{"\n\n".join(context_blocks)}"
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ]
        response = await asyncio.wait_for(
            asyncio.to_thread(
                self._chat_llm.invoke,
                messages,
                config={"metadata": {"user_email": user_email}},
            ),
            timeout=settings.LITELLM_HARD_TIMEOUT_SECONDS,
        )

        answer = str(response.content).strip() or "I could not produce an answer from the uploaded PDF."

        source_lines: list[str] = []
        seen: set[tuple[str, int]] = set()
        for i, chunk in enumerate(chunks, start=1):
            key = (chunk.source_name, chunk.chunk_index)
            if key in seen:
                continue
            seen.add(key)
            source_lines.append(f"- [{i}] {chunk.source_name} (chunk {chunk.chunk_index})")

        if source_lines:
            answer = f"{answer}\n\nSources:\n" + "\n".join(source_lines)
        return answer


_rag_service: RagService | None = None


def get_rag_service() -> RagService:
    """Get singleton RAG service."""
    global _rag_service
    if _rag_service is None:
        _rag_service = RagService()
    return _rag_service
