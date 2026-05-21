"""Autonomous research digest agent over arXiv with iterative reflection."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import io
import json
import logging
import re
import time
from typing import Awaitable, Callable

import arxiv
import httpx
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pypdf import PdfReader
from pydantic import BaseModel, ValidationError

from app.core.settings import settings
from app.schemas import (
    ResearchDigestCitation,
    ResearchDigestProgressEvent,
    ResearchDigestResult,
    ResearchDigestVisualization,
)

ProgressCallback = Callable[[ResearchDigestProgressEvent], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass
class ArxivPaper:
    """Paper metadata and extracted content used by the research loop."""

    title: str
    authors: list[str]
    published: str
    arxiv_id: str
    doi: str
    summary: str
    pdf_url: str
    category: str
    extracted_sections: dict[str, str]
    synthesized_summary: str = ""


class _ReflectionDecision(BaseModel):
    """Decision from reflection step."""

    continue_research: bool
    reason: str
    next_query: str
    confidence: float


class _DigestDraft(BaseModel):
    """Structured digest draft before adding citations and chart payloads."""

    executive_summary: str
    key_findings: list[str]
    evidence_assessment: str
    methodology_notes: str
    limitations: list[str]
    next_questions: list[str]


def _build_arxiv_client(page_size: int, *, delay_seconds: float = 2.0, num_retries: int = 0) -> arxiv.Client:
    """Use the arxiv library as the single integration surface for paper lookup."""
    return arxiv.Client(
        page_size=max(1, page_size),
        delay_seconds=delay_seconds,
        num_retries=num_retries,
    )


def _build_arxiv_topic_search(query: str, max_results: int) -> arxiv.Search:
    return arxiv.Search(
        query=f"all:{query}",
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
        sort_order=arxiv.SortOrder.Descending,
    )


def _build_arxiv_id_search(arxiv_ids: list[str]) -> arxiv.Search:
    return arxiv.Search(id_list=arxiv_ids, max_results=len(arxiv_ids))


def _result_to_paper(result: arxiv.Result) -> ArxivPaper:
    arxiv_id = result.entry_id.split("/abs/")[-1] if "/abs/" in result.entry_id else result.entry_id
    published = result.published.isoformat() if result.published else ""
    doi = result.doi or ""
    pdf_url = result.pdf_url or f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    category = result.primary_category or "unknown"
    return ArxivPaper(
        title=(result.title or "").strip()[:500],
        authors=[(author.name or "").strip()[:120] for author in result.authors],
        published=published[:100],
        arxiv_id=arxiv_id,
        doi=str(doi).strip()[:180],
        summary=(result.summary or "").strip()[:2000],
        pdf_url=pdf_url,
        category=category,
        extracted_sections={},
    )


# Tool definitions for LLM agent use
@tool
def search_arxiv_papers(query: str, max_results: int = 5) -> str:
    """
    Search arXiv for papers matching a query.
    
    Args:
        query: The search query (e.g., "machine learning", "quantum computing")
        max_results: Maximum number of results to return (default 5, max 20)
    
    Returns:
        A formatted string with paper titles, authors, and arxiv IDs
    """
    max_results = min(max_results, 20)
    client = _build_arxiv_client(max_results, delay_seconds=2.0, num_retries=2)
    search = _build_arxiv_topic_search(query, max_results)
    
    results = []
    for i, result in enumerate(client.results(search), 1):
        arxiv_id = result.entry_id.split("/abs/")[-1] if "/abs/" in result.entry_id else result.entry_id
        authors = ", ".join([a.name for a in result.authors[:3]]) + ("..." if len(result.authors) > 3 else "")
        results.append(
            f"{i}. [{arxiv_id}] {result.title}\n"
            f"   Authors: {authors}\n"
            f"   Published: {result.published.year if result.published else 'unknown'}"
        )
    
    return "\n".join(results) if results else "No papers found matching the query."


@tool
def get_paper_info(arxiv_id: str) -> str:
    """
    Retrieve detailed information about a specific paper.
    
    Args:
        arxiv_id: The arXiv ID of the paper (e.g., "2401.12345")
    
    Returns:
        Formatted string with paper title, authors, abstract, and URL
    """
    client = _build_arxiv_client(1, delay_seconds=2.0, num_retries=2)
    search = _build_arxiv_id_search([arxiv_id])
    
    for result in client.results(search):
        authors = ", ".join([a.name for a in result.authors])
        published = result.published.isoformat() if result.published else "unknown"
        pdf_url = result.pdf_url or f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        
        info = (
            f"Title: {result.title}\n"
            f"Authors: {authors}\n"
            f"Published: {published}\n"
            f"Category: {result.primary_category}\n"
            f"PDF URL: {pdf_url}\n"
            f"Abstract: {result.summary}"
        )
        return info
    
    return f"Paper with arXiv ID {arxiv_id} not found."


@tool
def extract_paper_sections(arxiv_id: str) -> str:
    """
    Extract key sections (introduction, methodology, results, conclusion) from a paper PDF.
    
    Args:
        arxiv_id: The arXiv ID of the paper
    
    Returns:
        Formatted string with extracted paper sections
    """
    client = _build_arxiv_client(1, delay_seconds=2.0, num_retries=2)
    search = _build_arxiv_id_search([arxiv_id])
    
    for result in client.results(search):
        pdf_url = result.pdf_url or f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        
        try:
            sync_client = httpx.Client(timeout=45.0)
            response = sync_client.get(pdf_url)
            response.raise_for_status()
            
            reader = PdfReader(io.BytesIO(response.content))
            extracted_pages: list[str] = []
            for page in reader.pages[:6]:
                try:
                    extracted_pages.append(page.extract_text() or "")
                except Exception:
                    extracted_pages.append("")
            
            raw_text = "\n".join(extracted_pages)
            if not raw_text.strip():
                return "Unable to extract text from PDF."
            
            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            text = "\n".join(lines)
            
            sections: dict[str, str] = {}
            section_patterns = {
                "introduction": r"(?is)\bintroduction\b(.*?)(\brelated work\b|\bmethod\b|\bbackground\b|\bexperiment\b|$)",
                "methodology": r"(?is)\b(method|approach|methodology)\b(.*?)(\bexperiment\b|\bresults\b|\bevaluation\b|$)",
                "results": r"(?is)\b(results|evaluation|experiments?)\b(.*?)(\bdiscussion\b|\bconclusion\b|$)",
                "conclusion": r"(?is)\b(conclusion|conclusions|future work)\b(.*)$",
            }
            
            for section_name, pattern in section_patterns.items():
                match = re.search(pattern, text)
                if match:
                    captured = next((g for g in match.groups() if g and len(g) > 30), "")
                    if captured:
                        sections[section_name] = captured[:1000]
            
            if not sections:
                sections["summary"] = text[:1000]
            
            return "\n\n".join(f"[{name.upper()}]\n{content}" for name, content in sections.items())
        
        except Exception as e:
            return f"Error extracting paper sections: {str(e)}"
    
    return f"Paper with arXiv ID {arxiv_id} not found."


class ResearchDigestService:
    """Iterative arXiv research agent with stopping criteria and digest synthesis."""

    def __init__(self) -> None:
        self._arxiv_lock = asyncio.Lock()
        self._last_arxiv_request_at = 0.0
        self._pdf_semaphore = asyncio.Semaphore(3)  # Max 3 concurrent PDF downloads
        self._search_cache: dict[str, list[ArxivPaper]] = {}  # Cache search results to reduce API hits

    def _build_llm(self) -> ChatOpenAI:
        base_url = settings.LITELLM_PROXY_URL.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        return ChatOpenAI(
            model=settings.LITELLM_CHAT_MODEL,
            api_key=settings.LITELLM_API_KEY,
            base_url=base_url,
            temperature=0,
            timeout=settings.LITELLM_TIMEOUT_SECONDS,
            max_retries=settings.LITELLM_MAX_RETRIES,
        )

    @staticmethod
    def _safe_text(value: str, limit: int = 4000) -> str:
        text = (value or "").strip()
        return text[:limit]

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError)):
            return True
        message = str(exc).lower()
        return "readtimeout" in message or "timed out" in message or "timeout" in message

    @staticmethod
    def _extract_arxiv_ids(text: str) -> list[str]:
        if not text:
            return []
        ids = re.findall(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", text)
        seen: set[str] = set()
        ordered: list[str] = []
        for value in ids:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered

    async def _get_papers_by_ids(self, arxiv_ids: list[str]) -> list[ArxivPaper]:
        if not arxiv_ids:
            return []

        unique_ids: list[str] = []
        seen: set[str] = set()
        for item in arxiv_ids:
            cleaned = item.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                unique_ids.append(cleaned)

        papers: list[ArxivPaper] = []
        chunk_size = 3
        max_attempts = 8
        backoff_seconds = 3.0

        logger.info("research_digest: fetching selected arxiv ids count=%s chunk_size=%s", len(unique_ids), chunk_size)

        for chunk_start in range(0, len(unique_ids), chunk_size):
            id_chunk = unique_ids[chunk_start:chunk_start + chunk_size]
            logger.info("research_digest: arxiv id chunk start=%s ids=%s", chunk_start, id_chunk)

            for attempt in range(1, max_attempts + 1):
                try:
                    async with self._arxiv_lock:
                        now = time.monotonic()
                        elapsed = now - self._last_arxiv_request_at
                        min_interval = 5.0
                        if elapsed < min_interval:
                            await asyncio.sleep(min_interval - elapsed)

                        client = _build_arxiv_client(
                            len(id_chunk),
                            delay_seconds=2.0,
                            num_retries=0,
                        )
                        search = _build_arxiv_id_search(id_chunk)
                        logger.info(
                            "research_digest: connecting to arxiv for id chunk attempt=%s ids=%s",
                            attempt,
                            id_chunk,
                        )

                        for result in client.results(search):
                            papers.append(_result_to_paper(result))

                        self._last_arxiv_request_at = time.monotonic()
                        break
                except Exception as exc:
                    error_msg = str(exc).lower()
                    logger.warning(
                        "research_digest: arxiv id chunk failed attempt=%s ids=%s error=%s",
                        attempt,
                        id_chunk,
                        exc,
                    )
                    if "429" in error_msg:
                        if attempt == max_attempts:
                            raise RuntimeError(
                                f"arXiv rate limited while fetching selected papers after {max_attempts} retries."
                            ) from exc
                        wait_time = backoff_seconds * (2 ** (attempt - 1))
                        await asyncio.sleep(wait_time)
                        continue

                    if self._is_timeout_error(exc):
                        if attempt < max_attempts:
                            await asyncio.sleep(backoff_seconds)
                            backoff_seconds *= 1.6
                            continue
                        raise

                    if any(code in error_msg for code in ["500", "502", "503", "504"]):
                        if attempt < max_attempts:
                            await asyncio.sleep(backoff_seconds)
                            backoff_seconds *= 1.6
                            continue
                        raise

                    raise

        return papers

    async def run_arxiv_agent(
        self,
        *,
        topic: str,
        user_email: str,
        max_iterations: int,
        papers_per_iteration: int,
        progress_callback: ProgressCallback,
    ) -> ResearchDigestResult:
        """Run research digest with an explicit arXiv tool-calling agent loop."""
        llm = self._build_llm()
        tool_llm = llm.bind_tools(get_research_tools())
        logger.info(
            "research_digest: starting arxiv agent topic=%r max_iterations=%s papers_per_iteration=%s",
            topic,
            max_iterations,
            papers_per_iteration,
        )

        messages = [
            SystemMessage(
                content=(
                    "You are an arXiv research agent. "
                    "Use tools to search papers and retrieve evidence before answering. "
                    "Prioritize recent and relevant papers, and include arXiv IDs in your response."
                )
            ),
            HumanMessage(
                content=(
                    f"Research topic: {topic}\n"
                    f"Target papers: {max(4, papers_per_iteration * max_iterations)}\n"
                    "Use tools first, then provide a short rationale and selected arXiv IDs."
                )
            ),
        ]

        selected_ids: list[str] = []
        agent_notes = ""
        max_steps = max(4, min(10, max_iterations * 3))

        tool_map = {tool_item.name: tool_item for tool_item in get_research_tools()}
        logger.info("research_digest: arxiv agent tools=%s", list(tool_map.keys()))

        for step in range(1, max_steps + 1):
            await progress_callback(
                ResearchDigestProgressEvent(
                    phase="agent",
                    message=f"arXiv agent step {step}: planning and tool usage",
                    iteration=step,
                    progress=min(0.1 + step * 0.06, 0.6),
                    details={
                        "stage": "llm_planning",
                        "step": step,
                        "tool_count": len(tool_map),
                    },
                )
            )
            logger.info("research_digest: agent step=%s invoking tool-capable llm", step)

            response = await tool_llm.ainvoke(
                messages,
                config={"metadata": {"user_email": user_email}},
            )
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None) or []
            if tool_calls:
                logger.info("research_digest: agent step=%s tool_calls=%s", step, [call.get("name", "") for call in tool_calls])
                for call in tool_calls[:4]:
                    tool_name = call.get("name", "")
                    tool_args = call.get("args", {}) or {}
                    tool_id = call.get("id", "")

                    await progress_callback(
                        ResearchDigestProgressEvent(
                            phase="agent",
                            message=f"Executing arXiv tool: {tool_name}",
                            iteration=step,
                            progress=min(0.14 + step * 0.06, 0.64),
                            details={
                                "stage": "tool_execution",
                                "tool_name": tool_name,
                                "tool_args": tool_args,
                                "tool_call_id": tool_id,
                            },
                        )
                    )

                    tool_impl = tool_map.get(tool_name)
                    if not tool_impl:
                        tool_output = f"Unknown tool: {tool_name}"
                    else:
                        try:
                            tool_output = str(tool_impl.invoke(tool_args))
                            logger.info(
                                "research_digest: tool completed name=%s output_preview=%r",
                                tool_name,
                                tool_output[:180],
                            )
                        except Exception as exc:
                            tool_output = f"Tool execution failed: {exc}"
                            logger.exception("research_digest: tool failed name=%s", tool_name)

                    selected_ids.extend(self._extract_arxiv_ids(tool_output))
                    messages.append(
                        ToolMessage(
                            content=self._safe_text(tool_output, 6000),
                            tool_call_id=tool_id,
                        )
                    )
                continue

            agent_notes = str(getattr(response, "content", "") or "")
            logger.info("research_digest: agent produced final planning response preview=%r", agent_notes[:200])
            selected_ids.extend(self._extract_arxiv_ids(agent_notes))
            if agent_notes.strip():
                break

        selected_ids = selected_ids[: max(4, papers_per_iteration * max_iterations)]

        await progress_callback(
            ResearchDigestProgressEvent(
                phase="search",
                message="Fetching selected papers from arXiv",
                iteration=1,
                progress=0.62,
                details={
                    "stage": "selected_paper_fetch",
                    "selected_ids": selected_ids[:8],
                    "selected_count": len(selected_ids),
                },
            )
        )
        logger.info("research_digest: selected_ids=%s", selected_ids)

        gathered_papers = await self._get_papers_by_ids(selected_ids)
        if not gathered_papers:
            # Fallback to direct topic search if agent response had no valid IDs.
            logger.info("research_digest: no selected ids resolved, falling back to topic search topic=%r", topic)
            gathered_papers = await self._search_arxiv(query=topic, start=0, max_results=max(4, papers_per_iteration))

        gathered_papers = gathered_papers[: max(4, papers_per_iteration * max_iterations)]
        logger.info("research_digest: gathered_papers=%s", len(gathered_papers))

        evidence_items: list[str] = []

        async def process_paper(paper: ArxivPaper) -> tuple[ArxivPaper, str]:
            await progress_callback(
                ResearchDigestProgressEvent(
                    phase="read_pdf",
                    message=f"Reading PDF sections: {paper.title}",
                    progress=0.7,
                    details={"arxiv_id": paper.arxiv_id},
                )
            )
            paper.extracted_sections = await self._read_pdf_sections(paper)
            await progress_callback(
                ResearchDigestProgressEvent(
                    phase="summarize",
                    message=f"Summarizing evidence: {paper.title}",
                    progress=0.8,
                    details={"arxiv_id": paper.arxiv_id},
                )
            )
            summary = await self._summarize_paper(llm=llm, paper=paper, user_email=user_email)
            return paper, summary

        processed = await asyncio.gather(
            *[process_paper(paper) for paper in gathered_papers],
            return_exceptions=True,
        )

        final_papers: list[ArxivPaper] = []
        for item in processed:
            if isinstance(item, Exception):
                continue
            paper, summary = item
            paper.synthesized_summary = summary
            final_papers.append(paper)
            evidence_items.append(
                f"Paper: {paper.title}\nID: {paper.arxiv_id}\nSummary:\n{paper.synthesized_summary}"
            )

        await progress_callback(
            ResearchDigestProgressEvent(
                phase="finalize",
                message="Synthesizing structured research digest",
                progress=0.95,
            )
        )

        draft = await self._generate_digest_draft(
            llm=llm,
            topic=topic,
            evidence_items=evidence_items,
            user_email=user_email,
        )

        citations = [
            self._build_citation(paper, idx, len(final_papers))
            for idx, paper in enumerate(final_papers)
        ]

        digest = ResearchDigestResult(
            topic=topic,
            executive_summary=draft.executive_summary,
            key_findings=draft.key_findings,
            evidence_assessment=draft.evidence_assessment,
            methodology_notes=draft.methodology_notes,
            limitations=draft.limitations,
            next_questions=draft.next_questions,
            citations=citations,
            visualizations=[],
            iterations_used=max(1, max_iterations),
            stopped_reason=agent_notes[:240] if agent_notes else "arXiv tool-agent completed",
            generated_at=datetime.now(tz=UTC),
        )

        await progress_callback(
            ResearchDigestProgressEvent(
                phase="complete",
                message="Research digest is ready",
                iteration=digest.iterations_used,
                progress=1.0,
                details={"citations": len(citations)},
            )
        )
        return digest

    async def _search_arxiv(self, query: str, start: int, max_results: int) -> list[ArxivPaper]:
        """Search arXiv using the arxiv library with aggressive rate-limiting and retry logic."""
        # Check cache first to reduce API calls
        cache_key = f"{query}:{max_results}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]
        
        max_attempts = 8
        backoff_seconds = 3.0
        papers: list[ArxivPaper] = []
        search_query = f"all:{query}"

        for attempt in range(1, max_attempts + 1):
            try:
                # arXiv API is aggressive with rate limiting - use longer delays
                async with self._arxiv_lock:
                    now = time.monotonic()
                    elapsed = now - self._last_arxiv_request_at
                    min_interval = 5.0  # Increased to 5s for arXiv politeness
                    if elapsed < min_interval:
                        await asyncio.sleep(min_interval - elapsed)

                    # Use arxiv.Search with client for query - increase internal delay
                    client = _build_arxiv_client(
                        max_results,
                        delay_seconds=2.0,
                        num_retries=0,
                    )
                    search = _build_arxiv_topic_search(query, max_results)

                    for result in client.results(search):
                        papers.append(_result_to_paper(result))

                    self._last_arxiv_request_at = time.monotonic()
                    # Cache successful result
                    self._search_cache[cache_key] = papers
                    return papers

            except Exception as exc:
                error_msg = str(exc).lower()
                
                # Handle 429 rate limit with exponential backoff
                if "429" in error_msg:
                    if attempt == max_attempts:
                        raise RuntimeError(
                            f"arXiv rate limited after {max_attempts} retries. "
                            "The service is being throttled. Please wait and retry in a few minutes."
                        ) from exc
                    
                    # Exponential backoff: 3s, 6s, 12s, 24s, 48s...
                    wait_time = backoff_seconds * (2 ** (attempt - 1))
                    await asyncio.sleep(wait_time)
                    continue
                
                # Handle timeout errors
                if self._is_timeout_error(exc):
                    if attempt < max_attempts:
                        await asyncio.sleep(backoff_seconds)
                        backoff_seconds *= 1.6
                        continue
                    raise
                
                # Handle other transient server errors
                if any(code in error_msg for code in ["500", "502", "503", "504"]):
                    if attempt < max_attempts:
                        await asyncio.sleep(backoff_seconds)
                        backoff_seconds *= 1.6
                        continue
                    raise

                raise

        return papers

    async def _read_pdf_sections(self, paper: ArxivPaper) -> dict[str, str]:
        if not paper.pdf_url:
            return {"abstract": paper.summary}

        try:
            # Limit concurrent PDF downloads to avoid overwhelming the server
            async with self._pdf_semaphore:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(paper.pdf_url)
                    response.raise_for_status()
                    content = response.content

            reader = PdfReader(io.BytesIO(content))
            extracted_pages: list[str] = []
            for page in reader.pages[:2]:  # Reduced from 4 to 2 pages for faster extraction
                try:
                    extracted_pages.append(page.extract_text() or "")
                except Exception:
                    extracted_pages.append("")

            raw_text = "\n".join(extracted_pages)
            if not raw_text.strip():
                return {"abstract": paper.summary}

            sections: dict[str, str] = {"abstract": paper.summary}
            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            text = "\n".join(lines)

            section_patterns = {
                "introduction": r"(?is)\bintroduction\b(.*?)(\brelated work\b|\bmethod\b|\bbackground\b|\bexperiment\b|$)",
                "method": r"(?is)\b(method|approach|methodology)\b(.*?)(\bexperiment\b|\bresults\b|\bevaluation\b|$)",
                "results": r"(?is)\b(results|evaluation|experiments?)\b(.*?)(\bdiscussion\b|\bconclusion\b|$)",
                "conclusion": r"(?is)\b(conclusion|conclusions|future work)\b(.*)$",
            }

            for section_name, pattern in section_patterns.items():
                match = re.search(pattern, text)
                if match:
                    captured = next((g for g in match.groups() if g and len(g) > 30), "")
                    if captured:
                        sections[section_name] = self._safe_text(captured, 1200)

            if "introduction" not in sections:
                sections["introduction"] = self._safe_text(text[:1200], 1200)

            return sections
        except Exception:
            return {"abstract": paper.summary}

    async def _summarize_paper(self, llm: ChatOpenAI, paper: ArxivPaper, user_email: str) -> str:
        section_dump = "\n\n".join(
            f"[{name.upper()}]\n{self._safe_text(content, 1200)}"
            for name, content in paper.extracted_sections.items()
        )
        prompt = (
            "You are summarizing a research paper for evidence collection. "
            "Return concise markdown with exactly these headings: "
            "- Core claim\n- Method\n- Key evidence\n- Limits\n"
            f"Title: {paper.title}\n"
            f"Abstract: {paper.summary}\n"
            f"Extracted sections:\n{section_dump}"
        )
        try:
            response = await llm.ainvoke(
                prompt,
                config={"metadata": {"user_email": user_email}},
            )
            return self._safe_text(getattr(response, "content", str(response)), 2500)
        except Exception as exc:
            if self._is_timeout_error(exc):
                return self._safe_text(
                    "Summary fallback used due to timeout. "
                    f"Abstract evidence: {paper.summary}",
                    2500,
                )
            raise

    async def _reflect(
        self,
        llm: ChatOpenAI,
        topic: str,
        evidence_items: list[str],
        current_query: str,
        iteration: int,
        user_email: str,
    ) -> _ReflectionDecision:
        evidence_blob = "\n\n".join(evidence_items[-8:])
        prompt = (
            "You are a research reflection controller. Decide if more evidence is needed. "
            "Respond as strict JSON with keys: continue_research (bool), reason (string), "
            "next_query (string), confidence (0-1).\n"
            f"Topic: {topic}\n"
            f"Current query: {current_query}\n"
            f"Iteration: {iteration}\n"
            f"Evidence:\n{evidence_blob}"
        )

        try:
            response = await llm.ainvoke(
                prompt,
                config={"metadata": {"user_email": user_email}},
            )
            content = getattr(response, "content", str(response))
        except Exception as exc:
            if self._is_timeout_error(exc):
                return _ReflectionDecision(
                    continue_research=False,
                    reason="Stopping due to model timeout; using gathered evidence.",
                    next_query=current_query,
                    confidence=0.7,
                )
            raise

        try:
            return _ReflectionDecision.model_validate_json(content)
        except ValidationError:
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                try:
                    return _ReflectionDecision.model_validate(json.loads(match.group(0)))
                except Exception:
                    pass

        # Safe fallback in case model returned invalid JSON
        return _ReflectionDecision(
            continue_research=False,
            reason="Stopping because reflection output was not parseable.",
            next_query=current_query,
            confidence=0.7,
        )

    async def _generate_digest_draft(
        self,
        llm: ChatOpenAI,
        topic: str,
        evidence_items: list[str],
        user_email: str,
    ) -> _DigestDraft:
        evidence_blob = "\n\n".join(evidence_items)
        prompt = (
            "You are producing a structured research digest for non-technical readers. "
            "Write in plain English like a short news explainer. Avoid code terms, internal process talk, and jargon. "
            "If a technical term is necessary, explain it in one simple sentence. "
            "Respond as strict JSON with keys: executive_summary (string), key_findings (array of strings), "
            "evidence_assessment (string), methodology_notes (string), limitations (array of strings), "
            "next_questions (array of strings).\n"
            "Requirements:\n"
            "- executive_summary: 2-4 short paragraphs, easy to read by a general audience.\n"
            "- key_findings: short bullet-like statements in everyday language.\n"
            "- evidence_assessment: plain-language confidence statement (for example: High/Medium/Low and why).\n"
            "- methodology_notes: one short plain-language line, no implementation details.\n"
            f"Topic: {topic}\n"
            f"Evidence snippets:\n{evidence_blob}"
        )

        try:
            response = await llm.ainvoke(
                prompt,
                config={"metadata": {"user_email": user_email}},
            )
            content = getattr(response, "content", str(response))
        except Exception as exc:
            if self._is_timeout_error(exc):
                return _DigestDraft(
                    executive_summary=(
                        "The digest was generated with partial model output because one or more "
                        "requests timed out. The summary below is based on collected evidence."
                    ),
                    key_findings=evidence_items[:5]
                    if evidence_items
                    else ["No evidence items were available before timeout."],
                    evidence_assessment="Medium (partial timeout fallback)",
                    methodology_notes="arXiv search + PDF extraction with timeout-safe fallback",
                    limitations=["One or more model calls timed out during synthesis."],
                    next_questions=["Re-run digest to attempt a fuller synthesis."],
                )
            raise

        try:
            return _DigestDraft.model_validate_json(content)
        except ValidationError:
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                return _DigestDraft.model_validate(json.loads(match.group(0)))

        return _DigestDraft(
            executive_summary="Evidence was collected but digest JSON parsing failed.",
            key_findings=["Unable to parse fully structured digest from model output."],
            evidence_assessment="Partial",
            methodology_notes="arXiv search + PDF section extraction + iterative reflection",
            limitations=["Model output schema parsing fallback path was used."],
            next_questions=["Re-run digest generation for a cleaner synthesis."],
        )

    @staticmethod
    def _extract_year(published: str) -> int | None:
        match = re.search(r"\b(19|20)\d{2}\b", published or "")
        return int(match.group(0)) if match else None

    @staticmethod
    def _apa_authors(authors: list[str]) -> str:
        if not authors:
            return "Unknown"
        parts: list[str] = []
        for name in authors[:8]:
            split = [item.strip() for item in name.split() if item.strip()]
            if not split:
                continue
            last = split[-1]
            initials = " ".join(f"{token[0]}." for token in split[:-1] if token)
            parts.append(f"{last}, {initials}".strip())
        if not parts:
            return "Unknown"
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]} & {parts[1]}"
        return ", ".join(parts[:-1]) + f", & {parts[-1]}"

    @staticmethod
    def _ieee_authors(authors: list[str]) -> str:
        if not authors:
            return "Unknown"
        formatted: list[str] = []
        for name in authors[:8]:
            split = [item.strip() for item in name.split() if item.strip()]
            if not split:
                continue
            last = split[-1]
            initials = " ".join(f"{token[0]}." for token in split[:-1] if token)
            formatted.append(f"{initials} {last}".strip())
        if not formatted:
            return "Unknown"
        if len(formatted) == 1:
            return formatted[0]
        return ", ".join(formatted[:-1]) + f", and {formatted[-1]}"

    @staticmethod
    def _bibtex_key(paper: ArxivPaper, idx: int) -> str:
        base = re.sub(r"[^a-zA-Z0-9]", "", (paper.authors[0] if paper.authors else "paper").lower())
        year = ResearchDigestService._extract_year(paper.published) or datetime.now(tz=UTC).year
        return f"{base}{year}{idx + 1}"

    def _build_citation(self, paper: ArxivPaper, idx: int, total: int) -> ResearchDigestCitation:
        year = self._extract_year(paper.published)
        apa_authors = self._apa_authors(paper.authors)
        ieee_authors = self._ieee_authors(paper.authors)
        year_text = str(year) if year else "n.d."
        title = self._safe_text(paper.title, 500)
        arxiv_url = f"https://arxiv.org/abs/{paper.arxiv_id}" if paper.arxiv_id else paper.pdf_url

        apa = f"{apa_authors} ({year_text}). {title}. arXiv. {arxiv_url}"
        ieee = f"{ieee_authors}, \"{title},\" arXiv, {year_text}. [Online]. Available: {arxiv_url}"

        key = self._bibtex_key(paper, idx)
        bibtex = (
            "@article{" + key + ",\n"
            + f"  title={{{title.replace('{', '').replace('}', '')}}},\n"
            + f"  author={{{' and '.join(paper.authors)}}},\n"
            + f"  year={{{str(year) if year else ''}}},\n"
            + f"  eprint={{{paper.arxiv_id}}},\n"
            + "  archivePrefix={arXiv},\n"
            + f"  primaryClass={{{paper.category}}},\n"
            + f"  url={{{arxiv_url}}},\n"
            + (f"  doi={{{paper.doi}}},\n" if paper.doi else "")
            + "}"
        )

        csl_json = {
            "id": f"C{idx + 1}",
            "type": "article-journal",
            "title": title,
            "author": [{"literal": author} for author in paper.authors],
            "issued": {"date-parts": [[year]]} if year else {},
            "DOI": paper.doi or "",
            "URL": arxiv_url,
            "publisher": "arXiv",
            "container-title": "arXiv preprint",
        }

        evidence_spans = [
            f"{name}: {self._safe_text(text, 220)}"
            for name, text in list(paper.extracted_sections.items())[:4]
            if text
        ]

        rank_weight = 1.0 - (idx / max(total, 1)) * 0.35

        return ResearchDigestCitation(
            citation_id=f"C{idx + 1}",
            title=title,
            authors=paper.authors,
            published=paper.published,
            year=year,
            arxiv_id=paper.arxiv_id,
            doi=paper.doi or None,
            pdf_url=paper.pdf_url,
            source_type="arxiv",
            relevance_score=round(max(0.2, min(1.0, rank_weight)), 2),
            evidence_spans=evidence_spans,
            apa_citation=apa,
            ieee_citation=ieee,
            bibtex_entry=bibtex,
            csl_json=csl_json,
            summary=self._safe_text(paper.synthesized_summary or paper.summary, 1200),
        )

    @staticmethod
    def _build_visualizations(citations: list[ResearchDigestCitation]) -> list[ResearchDigestVisualization]:
        year_counter: Counter[str] = Counter()
        category_counter: Counter[str] = Counter()

        for citation in citations:
            year_match = re.search(r"\b(19|20)\d{2}\b", citation.published)
            year = year_match.group(0) if year_match else "unknown"
            year_counter[year] += 1

            prefix = citation.arxiv_id.split(".", 1)[0] if "." in citation.arxiv_id else "other"
            category_counter[prefix] += 1

        by_year = ResearchDigestVisualization(
            title="Papers by Year",
            kind="bar",
            labels=list(year_counter.keys())[:12],
            values=list(year_counter.values())[:12],
        )
        by_prefix = ResearchDigestVisualization(
            title="arXiv ID Prefix Distribution",
            kind="bar",
            labels=list(category_counter.keys())[:12],
            values=list(category_counter.values())[:12],
        )
        return [by_year, by_prefix]

    async def run(
        self,
        *,
        topic: str,
        user_email: str,
        max_iterations: int,
        papers_per_iteration: int,
        progress_callback: ProgressCallback,
    ) -> ResearchDigestResult:
        llm = self._build_llm()
        query = topic
        seen_ids: set[str] = set()
        gathered_papers: list[ArxivPaper] = []
        evidence_items: list[str] = []
        stop_reason = "Reached iteration limit"

        for iteration in range(1, max_iterations + 1):
            await progress_callback(
                ResearchDigestProgressEvent(
                    phase="search",
                    message=f"Searching arXiv for iteration {iteration}: {query}",
                    iteration=iteration,
                    progress=min(0.1 + iteration * 0.15, 0.8),
                    details={"query": query},
                )
            )

            try:
                papers = await self._search_arxiv(
                    query=query,
                    start=(iteration - 1) * papers_per_iteration,
                    max_results=papers_per_iteration,
                )
            except Exception as exc:
                if self._is_timeout_error(exc):
                    stop_reason = "Stopping due to arXiv timeout; using evidence collected so far"
                    if gathered_papers:
                        break
                    raise RuntimeError(
                        "arXiv request timed out before any evidence could be gathered. Please retry."
                    ) from exc
                raise
            new_papers = [paper for paper in papers if paper.arxiv_id and paper.arxiv_id not in seen_ids]

            if not new_papers:
                stop_reason = "No new papers found"
                break

            # Mark papers as being processed
            for paper in new_papers:
                seen_ids.add(paper.arxiv_id)

            # Process all papers in parallel (PDF extraction + summarization)
            async def process_paper(paper: ArxivPaper) -> tuple[ArxivPaper, str]:
                await progress_callback(
                    ResearchDigestProgressEvent(
                        phase="read_pdf",
                        message=f"Reading PDF sections: {paper.title}",
                        iteration=iteration,
                        progress=min(0.2 + iteration * 0.15, 0.85),
                        details={"arxiv_id": paper.arxiv_id},
                    )
                )
                paper.extracted_sections = await self._read_pdf_sections(paper)

                await progress_callback(
                    ResearchDigestProgressEvent(
                        phase="summarize",
                        message=f"Summarizing evidence: {paper.title}",
                        iteration=iteration,
                        progress=min(0.3 + iteration * 0.15, 0.9),
                        details={"arxiv_id": paper.arxiv_id},
                    )
                )
                summary = await self._summarize_paper(llm=llm, paper=paper, user_email=user_email)
                return paper, summary

            # Run all paper processing tasks concurrently
            processed_results = await asyncio.gather(
                *[process_paper(paper) for paper in new_papers],
                return_exceptions=True,
            )

            for result in processed_results:
                if isinstance(result, Exception):
                    # Log error but continue with other papers
                    continue
                paper, summary = result
                paper.synthesized_summary = summary
                gathered_papers.append(paper)
                evidence_items.append(
                    f"Paper: {paper.title}\nID: {paper.arxiv_id}\nSummary:\n{paper.synthesized_summary}"
                )

            await progress_callback(
                ResearchDigestProgressEvent(
                    phase="reflect",
                    message=f"Reflecting on evidence sufficiency after iteration {iteration}",
                    iteration=iteration,
                    progress=min(0.4 + iteration * 0.15, 0.95),
                )
            )

            decision = await self._reflect(
                llm=llm,
                topic=topic,
                evidence_items=evidence_items,
                current_query=query,
                iteration=iteration,
                user_email=user_email,
            )

            # Stopping criteria: model says stop with adequate confidence, or enough evidence is gathered
            if not decision.continue_research and decision.confidence >= 0.6:
                stop_reason = decision.reason
                break
            if len(gathered_papers) >= max(6, papers_per_iteration * 2) and decision.confidence >= 0.85:
                stop_reason = "Evidence appears sufficient by confidence threshold"
                break

            query = (decision.next_query or query).strip()[:250] or query

        await progress_callback(
            ResearchDigestProgressEvent(
                phase="finalize",
                message="Synthesizing structured research digest",
                iteration=min(max_iterations, max(1, len(gathered_papers))),
                progress=0.97,
            )
        )

        citations = [
            self._build_citation(paper, idx, len(gathered_papers))
            for idx, paper in enumerate(gathered_papers)
        ]

        draft = await self._generate_digest_draft(
            llm=llm,
            topic=topic,
            evidence_items=evidence_items,
            user_email=user_email,
        )

        digest = ResearchDigestResult(
            topic=topic,
            executive_summary=draft.executive_summary,
            key_findings=draft.key_findings,
            evidence_assessment=draft.evidence_assessment,
            methodology_notes=draft.methodology_notes,
            limitations=draft.limitations,
            next_questions=draft.next_questions,
            citations=citations,
            visualizations=[],
            iterations_used=min(max_iterations, max(1, len(evidence_items))),
            stopped_reason=stop_reason,
            generated_at=datetime.now(tz=UTC),
        )

        await progress_callback(
            ResearchDigestProgressEvent(
                phase="complete",
                message="Research digest is ready",
                iteration=digest.iterations_used,
                progress=1.0,
                details={"citations": len(citations)},
            )
        )

        return digest


_research_digest_service: ResearchDigestService | None = None


def get_research_digest_service() -> ResearchDigestService:
    """Get singleton research digest service."""
    global _research_digest_service
    if _research_digest_service is None:
        _research_digest_service = ResearchDigestService()
    return _research_digest_service


def get_research_tools() -> list:
    """
    Get all available research tools for LLM agent use.
    
    Returns a list of tool functions that can be used with LangChain agents,
    LangGraph, or other agent frameworks.
    """
    return [
        search_arxiv_papers,
        get_paper_info,
        extract_paper_sections,
    ]
