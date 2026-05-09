"""LLM service for chat operations."""
import asyncio
import base64
import logging
import re
from typing import Any, Literal

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI

from app.core.settings import settings

logger = logging.getLogger(__name__)


class LLMService:
    """Service for LLM operations."""

    def __init__(self):
        """Initialize LLM client."""
        self.llm = self._build_chat_model()
        self.image_client = self._build_image_client()

    def _build_image_client(self) -> OpenAI:
        """Build OpenAI SDK client configured to route through LiteLLM proxy."""
        api_key = settings.LITELLM_API_KEY
        if not api_key:
            raise ValueError("LITELLM_API_KEY is not set. Add it to your .env file.")

        base_url = settings.LITELLM_PROXY_URL.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        return OpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def is_image_generation_request(message: str) -> bool:
        """Detect whether the current user prompt asks for image generation."""
        lowered = message.lower()
        # Catch both contiguous phrases ("generate image") and natural phrasing
        # like "generate AI family image" or "create a cinematic photo".
        has_intent = bool(
            re.search(
                r"\b(generate|create|make|render|draw|design)\b[\s\S]{0,80}\b(image|photo|picture|portrait|illustration|artwork|poster|logo)\b",
                lowered,
            )
        ) or bool(
            # Catch edit-like image requests such as
            # "add more family members for given image".
            re.search(
                r"\b(add|increase|expand|edit|modify|change|adjust|enhance)\b[\s\S]{0,120}\b(image|photo|picture|portrait|illustration|artwork)\b",
                lowered,
            )
        ) or any(
            token in lowered
            for token in [
                "image generation",
                "text to image",
                "t2i",
            ]
        ) or bool(
            # Catch bare "<subject> image" patterns like "moon image", "family image"
            re.search(
                r"^[\w\s]{1,40}\s+(image|photo|picture|portrait|illustration|artwork)\s*$",
                lowered.strip(),
            )
        )
        # Accept common structured-prompt formats such as:
        # "CONTEXT:", "## CONTEXT", "CONTEXT -", etc.
        has_context = bool(re.search(r"(^|\n)\s{0,4}(?:#+\s*)?context\s*[:\-]", lowered))
        has_goal = bool(re.search(r"(^|\n)\s{0,4}(?:#+\s*)?goal\s*[:\-]", lowered))
        has_output = bool(re.search(r"(^|\n)\s{0,4}(?:#+\s*)?output\s*[:\-]", lowered))
        has_structured_blocks = has_context and has_goal and has_output
        return has_intent or has_structured_blocks

    @staticmethod
    def is_followup_image_edit_request(message: str) -> bool:
        """Detect follow-up edit instructions likely targeting a prior image."""
        lowered = (message or "").lower().strip()
        if not lowered:
            return False

        # Dynamic: any edit verb followed by at least one substantive word counts.
        # No static noun whitelist — any object (prince, chair, bicycle...) is valid.
        has_edit_verb_with_content = bool(
            re.search(
                r"\b(add|include|insert|remove|replace|change|modify|edit|adjust|increase|expand|enhance)\b\s+\w",
                lowered,
            )
        )
        # "more X" pattern — concise add request without explicit verb (e.g. "more trees")
        has_more_noun = bool(re.search(r"\bmore\s+\w+", lowered))

        has_reference_phrase = bool(
            re.search(r"\b(previous|last|this|that|given)\s+(image|photo|picture|pic)\b", lowered)
        )

        has_image_description_phrase = bool(
            re.search(
                r"^(here\s+is\s+an?\s+image\s+featuring|image\s+featuring|family\s+with)\b",
                lowered,
            )
        )

        has_family_composition_phrase = bool(
            re.search(
                r"\b(parents?|grand\s*parents?|grandmother|grandfather|children|kids|family)\b",
                lowered,
            )
        )

        result = (
            has_edit_verb_with_content
            or has_more_noun
            or has_reference_phrase
            or (has_image_description_phrase and has_family_composition_phrase)
        )
        logger.info(
            f"is_followup_image_edit_request: message='{message[:80]}...' "
            f"has_edit_verb_with_content={has_edit_verb_with_content} has_more_noun={has_more_noun} "
            f"has_reference_phrase={has_reference_phrase} result={result}"
        )
        return result

    @staticmethod
    def _extract_prompt_sections(message: str) -> dict[str, str]:
        """Parse CONTEXT/GOAL/RULES/OUTPUT sections from user prompt text."""
        sections = {"context": "", "goal": "", "rules": "", "output": ""}
        current = None
        header_pattern = re.compile(
            r"^(?:#+\s*)?(context|goal|rules|output)\s*[:\-]\s*(.*)$",
            re.IGNORECASE,
        )

        for raw_line in message.splitlines():
            line = raw_line.strip()
            header_match = header_pattern.match(line)
            if header_match:
                current = header_match.group(1).lower()
                inline_value = (header_match.group(2) or "").strip()
                if inline_value:
                    sections[current] += f"{inline_value} "
                continue
            if current and line:
                sections[current] += f"{line} "

        return {key: value.strip() for key, value in sections.items()}

    @staticmethod
    def _extract_freeform_subject(message: str) -> str:
        """Extract likely visual subject from non-structured image request."""
        cleaned = (message or "").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        lowered = cleaned.lower()

        # Remove common instruction words while preserving subject nouns.
        lowered = re.sub(
            r"\b(generate|create|make|render|draw|design|please|an|a|the|image|photo|picture|portrait|illustration|artwork|of)\b",
            " ",
            lowered,
        )
        lowered = re.sub(r"\s+", " ", lowered).strip(" ,.-")

        if lowered:
            return lowered
        return cleaned if cleaned else "moon"

    @staticmethod
    def _extract_requested_people_count(message: str) -> int | None:
        """Extract requested number of people from free-form text when present."""
        lowered = (message or "").lower()

        direct_number = re.search(r"\b(\d{1,2})\s+(?:family\s+)?(?:members?|people|persons?)\b", lowered)
        if direct_number:
            return int(direct_number.group(1))

        word_to_number = {
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
        }
        for word, number in word_to_number.items():
            if re.search(rf"\b{word}\s+(?:family\s+)?(?:members?|people|persons?)\b", lowered):
                return number

        return None

    def _build_family_size_instruction(self, message: str) -> str:
        """Build explicit family-size constraints when user asks for more members."""
        lowered = (message or "").lower()
        if not re.search(r"\b(family|grand\s*parents?|grandparent|parents?|kids?|children)\b", lowered):
            return ""

        is_member_expansion_request = bool(
            re.search(r"\b(add|more|increase|larger|bigger|extended|expand)\b", lowered)
            and re.search(r"\b(member|members|people|person|grand\s*parents?|grandparent|parents?|kids?|children)\b", lowered)
        )
        if not is_member_expansion_request:
            return ""

        requested_count = self._extract_requested_people_count(message)
        if requested_count is not None:
            return (
                f"Family Composition Constraint: include exactly {requested_count} visible family members, "
                "with all faces clearly visible and naturally posed."
            )

        return (
            "Family Composition Constraint: include a clearly larger family group with at least 8 visible "
            "family members, all naturally posed and fully visible."
        )

    @staticmethod
    def _extract_quantity_and_item(message: str) -> tuple[str, str]:
        """Extract quantity indicator and item name from add/include requests."""
        lowered = (message or "").lower().strip()

        # Capture the actionable tail after an edit verb.
        verb_match = re.search(r"\b(add|include|increase|expand|fill|put|more)\b\s+(.*)", lowered)
        if not verb_match:
            # Support concise prompts like "more trees" without explicit verbs.
            concise_match = re.match(r"\b(more|some\s+more|additional|extra|several|many|multiple|one\s+more|two\s+more|three\s+more)\b\s+(.+)$", lowered)
            if concise_match:
                verb_match = concise_match
            else:
                return "", ""

        rest = (verb_match.group(2) or "").strip()
        if not rest:
            return "", ""

        # Remove trailing references that are not part of the object phrase.
        rest = re.sub(
            r"\b(to|for)\s+(the\s+)?(previous|last|given|current)\s+image\b.*$",
            "",
            rest,
        ).strip()

        quantity_desc = ""
        quantity_map = [
            ("one more", "one additional"),
            ("two more", "two additional"),
            ("three more", "three additional"),
            ("multiple", "multiple"),
            ("several", "several"),
            ("many", "many"),
            ("lots of", "many"),
            ("a few", "several"),
            ("few", "several"),
            ("some more", "multiple additional"),
            ("some", "several"),
            ("more", "multiple additional"),
            ("additional", "multiple additional"),
            ("extra", "multiple additional"),
            ("one", "a single"),
            ("a", "a single"),
            ("an", "a single"),
        ]

        for phrase, normalized in quantity_map:
            if rest == phrase or rest.startswith(f"{phrase} "):
                quantity_desc = normalized
                rest = rest[len(phrase):].strip()
                break

        # Numeric forms: "2 more books", "3 books"
        if not quantity_desc:
            numeric_match = re.match(r"(\d{1,2})\s+(more\s+)?(.+)$", rest)
            if numeric_match:
                count = numeric_match.group(1)
                quantity_desc = f"{count} additional"
                rest = numeric_match.group(3).strip()

        # Separate object from location phrases like "books in shelf".
        location_match = re.search(
            r"\b(in|on|at|inside|into|onto|near|by|within)\b\s+([a-z][a-z\s-]{0,40})$",
            rest,
        )
        if location_match:
            prep = location_match.group(1).strip()
            location = location_match.group(2).strip()
            item = rest[:location_match.start()].strip()
            location_phrase = f" {prep} {location}"
        else:
            item = rest.strip()
            location_phrase = ""

        # Drop leading style adjectives, keep the core noun phrase.
        item = re.sub(r"^(cute|adorable|beautiful|nice|new|extra|additional|more)\s+", "", item).strip()
        item = re.sub(r"\s+(please|now)$", "", item).strip()

        # If quantity was omitted but object looks plural, bias to multiple.
        if not quantity_desc and item.endswith("s") and not item.endswith("ss"):
            quantity_desc = "multiple"

        if not item:
            return quantity_desc, ""

        return quantity_desc, f"{item}{location_phrase}".strip()

    @staticmethod
    def _extract_requested_add_count(quantity_desc: str) -> int | None:
        """Convert normalized quantity text to an approximate additional-count target."""
        normalized = (quantity_desc or "").strip().lower()
        if not normalized:
            return None
        if normalized in {"a single", "one additional"}:
            return 1
        if normalized == "two additional":
            return 2
        if normalized == "three additional":
            return 3
        if normalized in {"multiple additional", "multiple", "several", "many", "at least 4 additional"}:
            return 4

        numeric = re.search(r"\b(\d{1,2})\s+additional\b", normalized)
        if numeric:
            return int(numeric.group(1))
        return None

    @staticmethod
    def _estimate_existing_item_count(reference_summary: str, item_phrase: str) -> int | None:
        """Estimate how many target items are already present in the reference summary."""
        if not reference_summary or not item_phrase:
            return None

        item_core = re.split(r"\b(in|on|at|inside|into|onto|near|by|within)\b", item_phrase, maxsplit=1)[0].strip()
        if not item_core:
            return None

        noun = item_core.split()[-1].strip()
        if not noun:
            return None

        # Loose singular/plural handling: tree <-> trees, bunny <-> bunnies, etc.
        singular = noun[:-1] if noun.endswith("s") and not noun.endswith("ss") else noun
        plural = noun if noun.endswith("s") else f"{noun}s"

        summary = (reference_summary or "").lower()
        best_count: int | None = None

        digit_match = re.findall(rf"\b(\d{{1,2}})\s+(?:{re.escape(singular)}|{re.escape(plural)})\b", summary)
        for value in digit_match:
            num = int(value)
            best_count = num if best_count is None else max(best_count, num)

        word_to_number = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
        }
        for word, num in word_to_number.items():
            if re.search(rf"\b{word}\s+(?:{re.escape(singular)}|{re.escape(plural)})\b", summary):
                best_count = num if best_count is None else max(best_count, num)

        return best_count

    def _build_more_count_instruction(self, message: str, reference_summary: str | None) -> str:
        """Build explicit count-delta instruction so 'more X' means higher X count."""
        quantity_desc, item_phrase = self._extract_quantity_and_item(message)
        if not item_phrase:
            return ""

        additional_count = self._extract_requested_add_count(quantity_desc)
        if additional_count is None and "more" in (message or "").lower():
            additional_count = 2

        if additional_count is None:
            return (
                f"Quantitative requirement: final image must show visibly more {item_phrase} than the reference image, "
                "not the same count."
            )

        existing_count = self._estimate_existing_item_count(reference_summary or "", item_phrase)
        if existing_count is not None:
            final_target = existing_count + additional_count
            return (
                f"Quantitative requirement: final image must contain at least {final_target} visible {item_phrase} in total "
                f"({existing_count} in reference + at least {additional_count} more)."
            )

        return (
            f"Quantitative requirement: add at least {additional_count} additional visible {item_phrase} compared to the reference image."
        )

    @staticmethod
    def _build_add_remove_constraints(message: str) -> str:
        """Build hard constraints for explicit add/remove edit instructions."""
        lowered = (message or "").lower()
        constraints: list[str] = []

        # Dynamic removal: detect any object the user wants removed.
        remove_match = re.search(
            r"\b(remove|without|exclude|delete|eliminate|no\s+more)\b\s+(?:the\s+|all\s+|any\s+)?(\w[\w\s]{0,40})$",
            lowered,
        ) or re.search(
            r"\bno\s+(\w[\w\s]{0,30})\b",
            lowered,
        )
        if remove_match:
            removed_item = remove_match.group(2 if remove_match.lastindex and remove_match.lastindex >= 2 else 1).strip()
            # Strip common trailing noise words
            removed_item = re.sub(r"\b(please|now|from\s+\w+|in\s+\w+)$", "", removed_item).strip()
            if removed_item:
                constraints.append(
                    f"MUST NOT include {removed_item} in the image.\n"
                    f"MUST NOT include {removed_item} in the image.\n"
                    f"MUST NOT include {removed_item} in the image."
                )
                logger.info(f"_build_add_remove_constraints: removed_item='{removed_item}'")

        # Dynamic inclusion: extract quantity + item generically from the message.
        # No static noun whitelist — works for ANY object the user names.
        quantity_desc, added_item = LLMService._extract_quantity_and_item(message)
        add_elements = bool(added_item)
        if add_elements:
            normalized_quantity = quantity_desc.strip().lower()
            quantity_text = quantity_desc
            # Map vague plurals to a concrete minimum count so the model understands scale.
            if normalized_quantity in {"multiple additional", "multiple", "several", "many"}:
                quantity_text = "at least 4 additional"

            if quantity_text:
                constraint_text = (
                    f"ADD IN ADDITION TO EXISTING CONTENT: {quantity_text} prominent, visible {added_item}.\n"
                    f"ADD IN ADDITION TO EXISTING CONTENT: {quantity_text} prominent, visible {added_item}.\n"
                    f"ADD IN ADDITION TO EXISTING CONTENT: {quantity_text} prominent, visible {added_item}."
                )
            else:
                constraint_text = (
                    f"ADD IN ADDITION TO EXISTING CONTENT: prominent, visible {added_item}.\n"
                    f"ADD IN ADDITION TO EXISTING CONTENT: prominent, visible {added_item}.\n"
                    f"ADD IN ADDITION TO EXISTING CONTENT: prominent, visible {added_item}."
                )

            constraints.append(constraint_text)
            logger.info(f"_build_add_remove_constraints: quantity_desc='{quantity_desc}' quantity_text='{quantity_text}' added_item='{added_item}'")

        result = "\n".join(constraints)
        logger.info(f"_build_add_remove_constraints: message='{message[:80]}...' result_len={len(result)} constraints_count={len(constraints)}")
        return result

    @staticmethod
    def _extract_all_subjects(message: str) -> list[str]:
        """Extract all subjects from a message, handling 'X and Y' multi-subject patterns."""
        # Remove instruction verbs and trailing noun like 'image/photo'
        cleaned = re.sub(
            r"\b(generate|create|make|render|draw|design|please|image|photo|picture|portrait|illustration|artwork)\b",
            " ",
            message.lower(),
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
        # Split on 'and' / '&' / ',' to get individual subjects
        parts = re.split(r"\band\b|&|,", cleaned)
        subjects = [p.strip() for p in parts if p.strip()]
        return subjects

    def _build_image_prompt(self, message: str, reference_summary: str | None = None) -> str:
        """Build a high-quality image prompt with explicit constraints."""
        # For follow-up edit requests, use a simpler direct prompt without reference interference
        add_remove_constraints = self._build_add_remove_constraints(message)
        if add_remove_constraints.strip():
            # Edit request detected - use a direct, simple prompt focused on new requirements
            return self._build_direct_edit_prompt(message, add_remove_constraints, reference_summary)

        sections = self._extract_prompt_sections(message)
        has_structured_sections = any(sections.values())
        freeform_subject = self._extract_freeform_subject(message)
        family_size_instruction = self._build_family_size_instruction(message)

        # ── Multi-subject shortcut ───────────────────────────────────────────
        # When the user asks for two or more subjects (e.g. "prince and princess"),
        # skip the structured boilerplate and write a tight natural-language scene
        # description so the model cannot pick just one subject.
        all_subjects = self._extract_all_subjects(message)
        if not has_structured_sections and len(all_subjects) >= 2:
            subjects_inline = " and ".join(all_subjects)
            subjects_list = "\n".join(f"  • {s}" for s in all_subjects)
            direct_prompt = (
                f"Generate a photorealistic image showing {subjects_inline} together in the same scene.\n\n"
                f"ALL of the following subjects MUST be clearly visible in the final image:\n{subjects_list}\n\n"
                "Each subject must be prominently placed and easily recognizable.\n"
                "Do NOT omit any subject. Do NOT show only one of them.\n\n"
                f"User prompt: {message.strip()}\n\n"
                "Output: 1536x1024, aspect ratio 3:2, natural lighting, high quality."
            )
            if reference_summary:
                direct_prompt += (
                    "\n\nBase the scene on this reference image:\n"
                    f"{reference_summary}\n"
                    "Preserve the style and composition while including all requested subjects."
                )
            return direct_prompt
        # ── End multi-subject shortcut ───────────────────────────────────────

        if not has_structured_sections and "moon" in freeform_subject:
            base_prompt = (
                "Generate a realistic image where the moon is the clear and dominant subject.\n"
                "Scene: dark night sky with subtle stars and soft cloud texture.\n"
                "Composition: moon centered or slightly off-center, clearly visible and sharp.\n"
                "Lighting: natural moon glow only.\n"
                "Do not include flowers, plants, people, buildings, or unrelated foreground subjects.\n"
                "Output: 1536x1024, aspect ratio 3:2, high detail, natural colors.\n\n"
                f"User Prompt (verbatim): {message.strip()}"
            )
            if reference_summary:
                base_prompt += (
                    "\n\nReference image summary (preserve overall composition/style while applying the new request):\n"
                    f"{reference_summary}"
                )
            return base_prompt

        context = sections["context"] or (
            f"primary subject: {freeform_subject}" if not has_structured_sections else "simple, clear scene"
        )
        goal = sections["goal"] or (
            f"generate an image that clearly and prominently depicts {freeform_subject}" if not has_structured_sections else "generate a clean and natural image"
        )
        rules = sections["rules"] or "keep composition simple and visually clear"
        output = sections["output"] or "resolution 1536x1024, aspect ratio 3:2"

        # Build mandatory subjects list — enumerate every subject so none gets dropped.
        all_subjects = self._extract_all_subjects(message)
        if len(all_subjects) > 1:
            mandatory_subjects_line = (
                "Mandatory subjects — ALL must appear clearly in the image:\n"
                + "\n".join(f"  - {s}" for s in all_subjects)
            )
            multi_subject_warning = (
                f"CRITICAL: This image MUST contain ALL {len(all_subjects)} subjects listed above. "
                "Do NOT omit any of them."
            )
        else:
            mandatory_subjects_line = f"Mandatory subject to include: {freeform_subject}."
            multi_subject_warning = "Do not replace the main subject with flowers, people, or unrelated objects."

        base_prompt = (
            "Create a single image using the following requirements. "
            "Keep the result simple and natural.\n\n"
            f"Subject/Scene Context: {context}.\n"
            f"Goal: {goal}.\n"
            f"Quality Requirements: {rules}.\n"
            f"Output Constraints: {output}.\n\n"
            "Avoid heavy stylization unless explicitly requested.\n\n"
            f"{mandatory_subjects_line}\n"
            f"{multi_subject_warning}\n\n"
            f"{family_size_instruction}\n\n"
            f"User Prompt (verbatim): {message.strip()}"
        )

        if reference_summary:
            base_prompt += (
                "\n\nReference image summary (use this as the base scene and preserve style/composition unless requested otherwise):\n"
                f"{reference_summary}\n"
                "Apply only the requested additions/changes while keeping the rest of the scene coherent. "
                "If any reference detail conflicts with hard constraints above, hard constraints win."
            )

        return base_prompt

    def _build_direct_edit_prompt(self, message: str, add_remove_constraints: str, reference_summary: str | None = None) -> str:
        """Build a direct prompt for follow-up edit requests that describes the final desired image."""
        if not reference_summary:
            # No reference image - just use the constraints
            return (
                f"{add_remove_constraints}\n\n"
                f"User request: {message.strip()}\n\n"
                "Generate a high-quality image following the requirements above.\n"
                "Use natural lighting and realistic composition. Output: 1536x1024, aspect ratio 3:2."
            )
        
        # Build a comprehensive prompt that describes the FINAL desired state
        # This is crucial - we describe what the image should contain, not just what to add
        more_count_instruction = self._build_more_count_instruction(message, reference_summary)
        prompt_parts = [
            "IMPORTANT: You are generating a COMPLETE image (not editing), based on these requirements:",
            "",
            "REFERENCE CONTENT (must be included in the generated image):",
            reference_summary,
            "",
            "ADDITIONS TO MAKE:",
            add_remove_constraints,
            "",
            f"USER REQUEST: {message.strip()}",
            "",
            "INSTRUCTIONS:",
            "• Generate a SINGLE unified image that includes BOTH:",
            "  - ALL elements from the reference content above",
            "  - The newly requested additions/modifications specified above",
            "• The image should look natural and coherent as a single scene",
            "• Maintain the same style, composition, and lighting as the reference",
            "• Use natural, realistic styling",
            f"• {more_count_instruction}" if more_count_instruction else "",
            "",
            "Generate the complete image now. Output: 1536x1024, aspect ratio 3:2."
        ]
        
        return "\n".join(prompt_parts)

    async def _summarize_reference_image(self, image_data_url: str, user_email: str) -> str | None:
        """Create a concise reference summary from an existing image for follow-up edits."""
        if not image_data_url:
            return None

        messages = [
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Describe this image for editing in 2 short lines: "
                            "main subjects, composition, lighting, and style."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    },
                ]
            )
        ]

        invoke_kwargs: dict[str, Any] = {"config": {"metadata": {"user_email": user_email}}}
        response = await asyncio.wait_for(
            asyncio.to_thread(self.llm.invoke, messages, **invoke_kwargs),
            timeout=settings.LITELLM_HARD_TIMEOUT_SECONDS,
        )
        summary = str(response.content).strip()
        if not summary:
            return None
        return summary[:700]

    async def generate_image(
        self,
        user_message: str,
        user_email: str,
        reference_image_data_url: str | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Generate an image through LiteLLM using Google Imagen model."""
        try:
            logger.info(f"generate_image called: message='{user_message[:80]}...' user_email='{user_email}'")
            
            reference_summary = None
            if reference_image_data_url:
                try:
                    reference_summary = await self._summarize_reference_image(
                        reference_image_data_url,
                        user_email,
                    )
                    logger.info(f"Reference image summary: {reference_summary}")
                except Exception as e:
                    logger.error(f"Failed to summarize reference image: {e}")
                    reference_summary = None

            prompt = self._build_image_prompt(user_message, reference_summary=reference_summary)
            
            # Log the complete prompt for debugging
            logger.info(f"\n{'='*80}\nIMAGE GENERATION PROMPT:\n{'='*80}\n{prompt}\n{'='*80}\n")

            def _request_image() -> Any:
                return self.image_client.images.generate(
                    model=settings.IMAGE_GEN_MODEL,
                    prompt=prompt,
                    size="1536x1024",
                    user=user_email,
                    extra_body={
                        "metadata": {
                            "application": settings.APP_NAME,
                            "environment": settings.ENVIRONMENT,
                        }
                    },
                )

            response = await asyncio.wait_for(
                asyncio.to_thread(_request_image),
                timeout=settings.LITELLM_HARD_TIMEOUT_SECONDS,
            )
            logger.info(f"Image generation API responded successfully")

            if not getattr(response, "data", None):
                raise ValueError("Image generation returned no image data")

            first = response.data[0]
            image_url = getattr(first, "url", None)
            b64_image = getattr(first, "b64_json", None)

            if b64_image:
                image_url = f"data:image/png;base64,{b64_image}"

            if image_url and not str(image_url).startswith("data:image/"):
                # Convert provider-hosted URLs to data URLs so old messages still render
                # after the original URL expires.
                try:
                    converted_data_url = await asyncio.wait_for(
                        asyncio.to_thread(self._download_image_as_data_url, image_url),
                        timeout=settings.LITELLM_HARD_TIMEOUT_SECONDS,
                    )
                    if converted_data_url:
                        image_url = converted_data_url
                except Exception:
                    # Fall back to provider URL if conversion fails.
                    pass

            if not image_url:
                raise ValueError("Image generation response missing URL and base64 payload")

            metadata = {
                "resolution": "1536x1024",
                "aspect_ratio": "3:2",
            }
            logger.info(f"Image generation successful: image_url length={len(image_url) if image_url else 0}")
            return image_url, metadata
        except Exception as e:
            logger.exception(f"Exception in generate_image: {e}")
            raise

    def _download_image_as_data_url(self, image_url: str) -> str | None:
        """Download image URL and convert to data URL for stable rendering."""
        if not image_url:
            return None

        with httpx.Client(
            timeout=settings.LITELLM_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = client.get(image_url)
            response.raise_for_status()

        mime_type = response.headers.get("content-type", "image/png").split(";")[0].strip()
        if not mime_type.startswith("image/"):
            return None

        encoded = base64.b64encode(response.content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

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
        self,
        messages_history: list[tuple[Literal["user", "assistant"], str]],
        current_message: str,
        image_data_urls: list[str] | None = None,
        user_email: str | None = None,
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

        # Add current message and optional image content blocks.
        if image_data_urls:
            content_blocks: list[dict[str, Any]] = [{"type": "text", "text": current_message}]
            for data_url in image_data_urls:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    }
                )
            messages.append(HumanMessage(content=content_blocks))
        else:
            messages.append(HumanMessage(content=current_message))

        invoke_kwargs: dict[str, Any] = {}
        if user_email:
            invoke_kwargs["config"] = {"metadata": {"user_email": user_email}}

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self.llm.invoke, messages, **invoke_kwargs),
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
