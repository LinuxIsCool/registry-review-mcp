"""DEPRECATED: Legacy Path 1 field extraction.

Superseded by unified LLM analysis in analyze_llm.py (Path 2).
Retained only because existing tests reference this module.
Scheduled for removal in a future cleanup pass.

LLM-powered field extraction for cross-document validation.

Uses Anthropic Claude API to extract structured fields from unstructured
evidence (markdown + images) with confidence scoring.
"""

import asyncio
import base64
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from ..config.settings import settings
from ..utils.cache import Cache

logger = logging.getLogger(__name__)

# Optional cost tracking (imported lazily to avoid circular dependencies)
_cost_tracker = None


def set_cost_tracker(tracker):
    """Set global cost tracker for this module.

    Args:
        tracker: CostTracker instance
    """
    global _cost_tracker
    _cost_tracker = tracker


def _track_api_call(
    model: str,
    extractor: str,
    document_name: str,
    usage: dict,
    duration: float,
    cached: bool = False,
) -> None:
    """Track an API call if cost tracker is enabled.

    Args:
        model: Model name
        extractor: Extractor type
        document_name: Document being processed
        usage: Usage dict from API response
        duration: Call duration in seconds
        cached: Whether this was a cache hit
    """
    if _cost_tracker:
        try:
            _cost_tracker.track_api_call(
                model=model,
                extractor=extractor,
                document_name=document_name,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                duration_seconds=duration,
                cached=cached,
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            )
        except Exception as e:
            logger.warning(f"Failed to track API call cost: {e}")


class ExtractedField(BaseModel):
    """Model for an extracted field with metadata."""

    value: Any
    field_type: str
    source: str  # "DOC-123, Page 5, Section 2.1"
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str  # Why this was extracted
    raw_text: str | None = None  # Original text snippet


class BaseExtractor:
    """Base class for LLM-powered field extractors.

    Provides shared functionality for chunking, image distribution, and caching.
    """

    def __init__(self, cache_namespace: str, client: AsyncAnthropic | None = None):
        """Initialize base extractor.

        Args:
            cache_namespace: Namespace for caching (e.g., "date_extraction")
            client: Optional AsyncAnthropic client (will create new one if not provided)
        """
        self.client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.cache = Cache(cache_namespace)

    async def _call_api_with_retry(
        self,
        api_call: Callable,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 32.0,
        **kwargs,
    ) -> Any:
        """Call Anthropic API with exponential backoff retry logic.

        Retries on transient errors:
        - RateLimitError (429)
        - InternalServerError (500+)
        - APIConnectionError (network issues)
        - APITimeoutError (timeouts)

        Does NOT retry on:
        - AuthenticationError (bad API key)
        - BadRequestError (malformed request)
        - Other client errors (4xx)

        Args:
            api_call: Async callable that makes the API call
            max_retries: Maximum number of retry attempts (default: 3)
            initial_delay: Initial retry delay in seconds (default: 1.0)
            max_delay: Maximum retry delay in seconds (default: 32.0)
            **kwargs: Arguments to pass to api_call

        Returns:
            API response

        Raises:
            Exception: Re-raises the last exception after all retries exhausted
        """
        delay = initial_delay
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                # Call the API
                return await api_call(**kwargs)

            except (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError) as e:
                last_exception = e
                error_type = type(e).__name__

                if attempt < max_retries:
                    # Add jitter to prevent thundering herd (±25% of delay)
                    jitter = delay * 0.25 * (2 * random.random() - 1)
                    sleep_time = min(delay + jitter, max_delay)

                    logger.warning(
                        f"API call failed with {error_type} (attempt {attempt + 1}/{max_retries + 1}). "
                        f"Retrying in {sleep_time:.2f}s... Error: {str(e)}"
                    )

                    await asyncio.sleep(sleep_time)

                    # Exponential backoff: double the delay for next attempt
                    delay = min(delay * 2, max_delay)
                else:
                    logger.error(
                        f"API call failed after {max_retries + 1} attempts. Final error: {error_type}: {str(e)}"
                    )
                    raise

            except Exception as e:
                # Non-retryable error (auth, validation, etc.) - fail immediately
                logger.error(f"API call failed with non-retryable error: {type(e).__name__}: {str(e)}")
                raise

        # Should never reach here, but if we do, raise the last exception
        if last_exception:
            raise last_exception

    def _chunk_content(self, content: str) -> list[str]:
        """Split content into overlapping chunks if needed.

        Args:
            content: Full document content

        Returns:
            List of content chunks
        """
        if len(content) <= settings.llm_max_input_chars:
            return [content]

        if not settings.llm_enable_chunking:
            logger.warning(
                f"Content length {len(content)} exceeds limit {settings.llm_max_input_chars}, "
                f"truncating (chunking disabled)"
            )
            return [content[: settings.llm_max_input_chars]]

        # Split into overlapping chunks
        chunk_size = settings.llm_chunk_size
        overlap = settings.llm_chunk_overlap

        # Validate chunking parameters
        if overlap >= chunk_size:
            raise ValueError(f"Chunk overlap ({overlap}) must be less than chunk size ({chunk_size})")

        logger.info(
            f"Content length {len(content)} exceeds limit, splitting into boundary-aware chunks "
            f"(size: {chunk_size}, overlap: {overlap})"
        )

        chunks = []
        start = 0
        while start < len(content):
            # Find natural boundary near target chunk size
            end = min(start + chunk_size, len(content))

            # If not at document end, find best split point
            if end < len(content):
                end = self._find_split_boundary(content, start, end)

            # Ensure end is reasonable (prevent infinite loop and tiny chunks)
            # If boundary detection failed badly, fall back to simple chunking
            min_reasonable_end = start + (chunk_size // 2)
            if end < min_reasonable_end and end < len(content):
                end = min(start + chunk_size, len(content))

            chunks.append(content[start:end])

            # Move to next chunk with overlap
            # Always advance by at least chunk_size // 10 to prevent tiny chunks
            advance = (end - start) - overlap
            if advance < chunk_size // 10:
                advance = chunk_size - overlap
            start += advance

        logger.info(f"Created {len(chunks)} boundary-aware chunks from {len(content)} characters")
        return chunks

    def _find_split_boundary(self, content: str, start: int, target_end: int) -> int:
        """Find natural boundary for splitting content.

        Searches within ±500 chars of target_end for:
        1. Paragraph break (double newline) - highest priority
        2. Sentence end (. ! ? followed by space/newline)
        3. Word boundary (space)

        Args:
            content: Full content string
            start: Start position of chunk
            target_end: Target end position (may be adjusted)

        Returns:
            Adjusted end position at natural boundary
        """
        search_window = 500  # chars to search around target_end
        # Minimum viable chunk size: 20% of configured chunk_size
        min_chunk = min(settings.llm_chunk_size // 5, 10000)

        # Don't split too early (ensure minimum chunk size)
        if target_end - start < min_chunk:
            return target_end

        # Search backward from target for best boundary
        search_start = max(start + min_chunk, target_end - search_window)
        search_region = content[search_start:target_end]

        # 1. Try paragraph break (double newline)
        para_idx = search_region.rfind("\n\n")
        if para_idx != -1:
            return search_start + para_idx + 2  # After the double newline

        # 2. Try sentence boundary (. ! ? followed by space/newline)
        for i in range(len(search_region) - 1, 0, -1):
            if search_region[i - 1 : i] in ".!?" and search_region[i : i + 1] in " \n":
                return search_start + i + 1  # Include the space/newline after punctuation

        # 3. Try word boundary (space)
        space_idx = search_region.rfind(" ")
        if space_idx != -1:
            return search_start + space_idx

        # Fallback: use target position
        return target_end

    def _distribute_images(self, images: list[Path], num_chunks: int) -> list[list[Path]]:
        """Distribute images across chunks.

        Args:
            images: List of image paths
            num_chunks: Number of content chunks

        Returns:
            List of image lists, one per chunk
        """
        if not images:
            return [[] for _ in range(num_chunks)]

        # Warn if many images (cost consideration)
        if len(images) > settings.llm_warn_image_threshold:
            logger.warning(
                f"Processing {len(images)} images will incur significant API costs. "
                f"Consider reducing images or increasing llm_max_images_per_call limit."
            )

        # Distribute images evenly across chunks
        images_per_chunk = settings.llm_max_images_per_call
        distributed = []

        for i in range(num_chunks):
            start_idx = i * images_per_chunk
            end_idx = min(start_idx + images_per_chunk, len(images))
            chunk_images = images[start_idx:end_idx] if start_idx < len(images) else []
            distributed.append(chunk_images)

        # Log if we had to skip images
        total_processed = sum(len(img_list) for img_list in distributed)
        if total_processed < len(images):
            logger.warning(
                f"Only processing {total_processed}/{len(images)} images due to limit "
                f"(llm_max_images_per_call={settings.llm_max_images_per_call}). "
                f"Increase limit or process in multiple sessions."
            )

        return distributed


# Date extraction prompt
DATE_EXTRACTION_PROMPT = """You are a date extraction specialist for carbon credit project reviews.

Extract ALL dates from documents and classify each by type.

Date Types:
- project_start_date: When the project officially began
- crediting_period_start: Beginning of crediting period
- crediting_period_end: End of crediting period
- imagery_date: When satellite/aerial imagery was acquired
- sampling_date: When soil/field sampling occurred
- baseline_date: When baseline assessment was conducted
- monitoring_date: When monitoring report was completed
- submission_date: When documents were submitted

Instructions:
1. Find ALL date mentions in the document
2. Use context to determine the correct date type
3. Parse dates in ANY format (MM/DD/YYYY, "August 15 2022", etc.)
4. Handle ranges ("January 1, 2022 - December 31, 2031")
5. Assign confidence based on context clarity (1.0 = explicit, 0.8 = inferred, 0.5 = ambiguous)

Return JSON array:
[
  {
    "value": "2022-01-01",
    "field_type": "project_start_date",
    "source": "Section 1.8",
    "confidence": 0.95,
    "reasoning": "Document explicitly states 'Project Start Date: 01/01/2022'",
    "raw_text": "Project Start Date: 01/01/2022"
  }
]

CRITICAL: Only extract dates actually present in the document. Do not infer or assume dates."""


# Land tenure extraction prompt
LAND_TENURE_EXTRACTION_PROMPT = """You are a land tenure specialist for carbon credit project reviews.

Extract land ownership information from documents and images (including scanned land titles).

Fields to Extract:
- owner_name: Name of landowner or leaseholder
- area_hectares: Total area in hectares
- tenure_type: Type of land tenure (ownership, lease, easement, etc.)
- ownership_percentage: Percentage of ownership (if mentioned)

Instructions:
1. Extract ALL land tenure information from text AND images
2. Handle name variations (e.g., "Nick" = "Nicholas", "N. Denman" = "Nicholas Denman")
3. Read scanned land title images using OCR
4. Normalize names to full form when possible
5. Parse areas in any unit (hectares, acres - convert to hectares: 1 acre = 0.404686 ha)
6. Assign confidence based on source quality (1.0 = official document/title, 0.8 = project plan, 0.5 = unclear)

Special Cases:
- "maps dating" or "maps dating data" is NOT a person name - exclude these
- Multiple owners: create separate entries for each
- Trust names: extract both trust name and trustee if available

Return JSON array:
[
  {
    "value": "Nicholas Denman",
    "field_type": "owner_name",
    "source": "Land Title Document, Page 1",
    "confidence": 1.0,
    "reasoning": "Name clearly visible on scanned land title certificate",
    "raw_text": "Registered Owner: Nicholas Denman"
  },
  {
    "value": 120.5,
    "field_type": "area_hectares",
    "source": "Project Plan, Section 1.2",
    "confidence": 0.95,
    "reasoning": "Total project area explicitly stated",
    "raw_text": "Total Area: 120.5 hectares"
  }
]

CRITICAL: Only extract information actually present. Do not infer or create data."""


# Project ID extraction prompt
PROJECT_ID_EXTRACTION_PROMPT = r"""You are a project ID specialist for carbon credit registries.

Extract ALL project ID references from documents. Project IDs are unique identifiers used by various carbon registries.

Common Patterns:
- Regen Network: C06-4997, C06-4998 (format: C\d{2}-\d+)
- Verra (VCS): VCS-1234, VCS1234 (format: VCS-?\d+)
- Gold Standard: GS-5678, GS5678 (format: GS-?\d+)
- Climate Action Reserve (CAR): CAR1234 (format: CAR\d+)
- American Carbon Registry (ACR): ACR123 (format: ACR\d+)
- Custom/Internal: Any alphanumeric ID that appears repeatedly

Instructions:
1. Extract ALL project ID mentions across all documents
2. Identify the ID pattern (which registry standard)
3. Include page numbers and sections for each occurrence
4. Note if ID appears in headers, footers, or body text
5. Flag any inconsistencies (different IDs in same project)
6. Assign confidence based on pattern clarity (1.0 = matches known registry, 0.8 = custom ID, 0.5 = ambiguous)

Special Cases - DO NOT EXTRACT:
- Reference numbers (e.g., REQ-007, DOC-001) are NOT project IDs
- Version numbers (e.g., v1.2.2) are NOT project IDs
- Dates (e.g., 2022-01-01) are NOT project IDs
- Document filename prefixes (e.g., "4997Botany22" in "Documents Submitted: 4997Botany22_Project_Plan.pdf")
- Standalone numbers without registry prefix (e.g., just "4997" or "4998")

ONLY extract IDs that appear as "Project ID: [value]" or match known registry patterns (C##-####, VCS####, etc.).

Return JSON array:
[
  {
    "value": "C06-4997",
    "field_type": "project_id",
    "source": "Project Plan, Page 1, Header",
    "confidence": 1.0,
    "reasoning": "Matches Regen Network pattern (C\d{2}-\d+), appears in document header",
    "raw_text": "Project ID: C06-4997"
  },
  {
    "value": "C06-4997",
    "field_type": "project_id",
    "source": "Baseline Report, Page 3, Section 1.1",
    "confidence": 1.0,
    "reasoning": "Same ID referenced in project overview section",
    "raw_text": "This report covers project C06-4997"
  }
]

CRITICAL: Only extract actual project IDs. Do not confuse with requirement IDs, document IDs, or version numbers."""


class DateExtractor(BaseExtractor):
    """Extract dates from documents with context awareness."""

    def __init__(self, client: AsyncAnthropic | None = None):
        """Initialize date extractor.

        Args:
            client: Anthropic client (creates one if not provided)
        """
        super().__init__(cache_namespace="date_extraction", client=client)

    async def _process_date_chunk(
        self, chunk: str, chunk_images: list[Path], chunk_name: str, chunk_index: int
    ) -> list[ExtractedField]:
        """Process a single chunk of content for date extraction.

        Args:
            chunk: Text content for this chunk
            chunk_images: List of image paths for this chunk
            chunk_name: Human-readable name for logging
            chunk_index: Index of this chunk

        Returns:
            List of extracted date fields from this chunk
        """
        # Build message content (text + images)
        content = [
            {
                "type": "text",
                "text": f"Document: {chunk_name}\n\n{chunk}",
            }
        ]

        # Add images for this chunk
        for img_path in chunk_images:
            if img_path.exists():
                try:
                    img_data = base64.standard_b64encode(img_path.read_bytes()).decode("utf-8")
                    content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_data,
                            },
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to load image {img_path}: {e}")

        # Call Anthropic API with retry logic and prompt caching
        # Mark system prompt for caching to save 90% on repeated extractions
        try:
            start_time = time.time()
            response = await self._call_api_with_retry(
                self.client.messages.create,
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                system=[{"type": "text", "text": DATE_EXTRACTION_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                timeout=settings.api_call_timeout_seconds,
            )
            duration = time.time() - start_time

            # Track cost if tracker is enabled
            _track_api_call(
                model=settings.llm_model,
                extractor="date",
                document_name=chunk_name,
                usage=response.usage.model_dump() if hasattr(response, "usage") else {},
                duration=duration,
                cached=False,
            )

            # Parse response
            response_text = response.content[0].text

            # Extract and validate JSON from response
            json_str = extract_json_from_response(response_text)
            extracted_data = validate_and_parse_extraction_response(json_str, "date")

            # Verify citations against source content (prevent hallucination)
            from ..extractors.verification import verify_date_extraction

            verified_data = verify_date_extraction(extracted_data, chunk)

            # Convert to ExtractedField objects
            chunk_fields = [ExtractedField(**data) for data in verified_data]

            logger.info(f"Extracted {len(chunk_fields)} dates from {chunk_name}")
            return chunk_fields

        except ValueError as e:
            # Invalid JSON or structure - log and continue
            logger.error(f"Invalid response from LLM for {chunk_name}: {e}")
            return []
        except Exception as e:
            logger.error(f"Date extraction failed for {chunk_name}: {e}", exc_info=True)
            raise  # Re-raise to be caught by asyncio.gather

    async def extract(self, markdown_content: str, images: list[Path], document_name: str) -> list[ExtractedField]:
        """Extract dates from markdown and images.

        Args:
            markdown_content: Document text in markdown format
            images: List of paths to images
            document_name: Name of the document being processed

        Returns:
            List of extracted date fields
        """
        # Check cache
        cache_key = f"{document_name}_dates"
        if cached := self.cache.get(cache_key):
            logger.debug(f"Cache hit for {document_name} dates")
            # Track cache hit (zero cost)
            _track_api_call(
                model=settings.llm_model,
                extractor="date",
                document_name=document_name,
                usage={},
                duration=0.0,
                cached=True,
            )
            return [ExtractedField(**f) for f in cached]

        # Split content into chunks if needed
        chunks = self._chunk_content(markdown_content)

        # Distribute images across chunks
        chunk_images = self._distribute_images(images, len(chunks))

        # Process all chunks in parallel using asyncio.gather
        chunk_tasks = []
        for i, chunk in enumerate(chunks):
            chunk_name = f"{document_name} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else document_name
            task = self._process_date_chunk(chunk, chunk_images[i], chunk_name, i)
            chunk_tasks.append(task)

        # Execute all chunk processing tasks concurrently
        chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

        # Collect successful results and log failures
        all_fields = []
        for i, result in enumerate(chunk_results):
            if isinstance(result, Exception):
                chunk_name = f"{document_name} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else document_name
                logger.error(f"Date extraction failed for {chunk_name}: {result}", exc_info=result)
            elif result:  # result is list of ExtractedField objects
                all_fields.extend(result)

        # Deduplicate fields (same field_type + value, keep highest confidence)
        deduplicated = {}
        for field in all_fields:
            key = (field.field_type, str(field.value))
            if key not in deduplicated or field.confidence > deduplicated[key].confidence:
                deduplicated[key] = field

        fields = list(deduplicated.values())

        # Cache results
        self.cache.set(cache_key, [f.model_dump() for f in fields])

        logger.info(f"Extracted {len(fields)} unique dates from {document_name} ({len(all_fields)} total before dedup)")
        return fields


class LandTenureExtractor(BaseExtractor):
    """Extract land tenure information from documents and images."""

    def __init__(self, client: AsyncAnthropic | None = None):
        """Initialize land tenure extractor.

        Args:
            client: Optional AsyncAnthropic client (will create new one if not provided)
        """
        super().__init__(cache_namespace="land_tenure_extraction", client=client)

    async def extract(self, markdown_content: str, images: list[Path], document_name: str) -> list[ExtractedField]:
        """Extract land tenure information from markdown and images.

        Args:
            markdown_content: Document text in markdown format
            images: List of paths to images (land titles, maps, etc.)
            document_name: Name of the document being processed

        Returns:
            List of extracted land tenure fields
        """
        # Check cache
        cache_key = f"{document_name}_tenure"
        if cached := self.cache.get(cache_key):
            logger.debug(f"Cache hit for {document_name} tenure")
            # Track cache hit (zero cost)
            _track_api_call(
                model=settings.llm_model,
                extractor="tenure",
                document_name=document_name,
                usage={},
                duration=0.0,
                cached=True,
            )
            return [ExtractedField(**f) for f in cached]

        # Split content and distribute images
        chunks = self._chunk_content(markdown_content)
        chunk_images = self._distribute_images(images, len(chunks))

        all_fields = []
        for i, chunk in enumerate(chunks):
            chunk_name = f"{document_name} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else document_name

            # Build message content (text + images)
            content = [
                {
                    "type": "text",
                    "text": f"Document: {chunk_name}\n\n{chunk}",
                }
            ]

            # Add images for this chunk (especially important for land titles)
            for img_path in chunk_images[i]:
                if img_path.exists():
                    try:
                        img_data = base64.standard_b64encode(img_path.read_bytes()).decode("utf-8")
                        # Detect media type
                        media_type = "image/jpeg"
                        if img_path.suffix.lower() in [".png"]:
                            media_type = "image/png"
                        elif img_path.suffix.lower() in [".webp"]:
                            media_type = "image/webp"

                        content.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": img_data,
                                },
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to load image {img_path}: {e}")

            # Call Anthropic API with retry logic and prompt caching
            try:
                start_time = time.time()
                response = await self._call_api_with_retry(
                    self.client.messages.create,
                    model=settings.llm_model,
                    max_tokens=settings.llm_max_tokens,
                    temperature=settings.llm_temperature,
                    system=[
                        {"type": "text", "text": LAND_TENURE_EXTRACTION_PROMPT, "cache_control": {"type": "ephemeral"}}
                    ],
                    messages=[{"role": "user", "content": content}],
                    timeout=settings.api_call_timeout_seconds,
                )
                duration = time.time() - start_time

                # Track cost if tracker is enabled
                _track_api_call(
                    model=settings.llm_model,
                    extractor="tenure",
                    document_name=chunk_name,
                    usage=response.usage.model_dump() if hasattr(response, "usage") else {},
                    duration=duration,
                    cached=False,
                )

                # Parse response
                response_text = response.content[0].text

                # Extract and validate JSON from response
                json_str = extract_json_from_response(response_text)
                extracted_data = validate_and_parse_extraction_response(json_str, "tenure")

                # Convert to ExtractedField objects
                chunk_fields = [ExtractedField(**data) for data in extracted_data]
                all_fields.extend(chunk_fields)

                logger.info(f"Extracted {len(chunk_fields)} tenure fields from {chunk_name}")

            except ValueError as e:
                # Invalid JSON or structure - log and continue
                logger.error(f"Invalid response from LLM for {chunk_name}: {e}")
            except Exception as e:
                logger.error(f"Land tenure extraction failed for {chunk_name}: {e}", exc_info=True)

        # Deduplicate fields with fuzzy matching for owner names
        deduplicated = {}

        for field in all_fields:
            # For owner_name fields, use fuzzy matching to handle name variations
            if field.field_type == "owner_name" and settings.land_tenure_fuzzy_match:
                # Check if any existing owner name is similar
                matched_key = None
                best_similarity = 0.0

                for existing_key in deduplicated:
                    if existing_key[0] == "owner_name":  # Same field type
                        existing_name = str(existing_key[1])
                        current_name = str(field.value)

                        # Use combination of partial_ratio and token_set_ratio for best name matching
                        # partial_ratio handles abbreviations well (Nick vs Nicholas)
                        # token_set_ratio handles word order variations
                        partial_sim = fuzz.partial_ratio(existing_name.lower(), current_name.lower()) / 100.0
                        token_sim = fuzz.token_set_ratio(existing_name.lower(), current_name.lower()) / 100.0
                        similarity = max(partial_sim, token_sim)

                        if similarity > best_similarity:
                            best_similarity = similarity
                            matched_key = existing_key

                # If similarity above threshold (75%), treat as duplicate
                # Threshold accounts for common name variations (Nick vs Nicholas = 78%)
                if best_similarity >= 0.75 and matched_key:
                    # Keep the one with higher confidence
                    if field.confidence > deduplicated[matched_key].confidence:
                        # Replace with higher confidence version
                        del deduplicated[matched_key]
                        deduplicated[(field.field_type, str(field.value))] = field
                        logger.debug(
                            f"Fuzzy dedup: Replaced '{matched_key[1]}' with '{field.value}' "
                            f"(similarity: {best_similarity:.2f}, confidence: {field.confidence})"
                        )
                else:
                    # New unique name
                    deduplicated[(field.field_type, str(field.value))] = field
            else:
                # For non-name fields, use exact matching
                key = (field.field_type, str(field.value))
                if key not in deduplicated or field.confidence > deduplicated[key].confidence:
                    deduplicated[key] = field

        fields = list(deduplicated.values())

        # Cache results
        self.cache.set(cache_key, [f.model_dump() for f in fields])

        logger.info(
            f"Extracted {len(fields)} unique tenure fields from {document_name} ({len(all_fields)} total before dedup)"
        )
        return fields


class ProjectIDExtractor(BaseExtractor):
    """Extract project IDs from documents for consistency validation."""

    def __init__(self, client: AsyncAnthropic | None = None):
        """Initialize project ID extractor.

        Args:
            client: Optional AsyncAnthropic client (will create new one if not provided)
        """
        super().__init__(cache_namespace="project_id_extraction", client=client)

    async def extract(self, markdown_content: str, images: list[Path], document_name: str) -> list[ExtractedField]:
        """Extract project IDs from markdown and images.

        Args:
            markdown_content: Document text in markdown format
            images: List of paths to images
            document_name: Name of the document being processed

        Returns:
            List of extracted project ID fields
        """
        # Check cache
        cache_key = f"{document_name}_project_ids"
        if cached := self.cache.get(cache_key):
            logger.debug(f"Cache hit for {document_name} project IDs")
            # Track cache hit (zero cost)
            _track_api_call(
                model=settings.llm_model,
                extractor="project_id",
                document_name=document_name,
                usage={},
                duration=0.0,
                cached=True,
            )
            return [ExtractedField(**f) for f in cached]

        # Split content into chunks if needed
        chunks = self._chunk_content(markdown_content)

        # Distribute images across chunks
        chunk_images = self._distribute_images(images, len(chunks))

        all_fields = []
        for i, chunk in enumerate(chunks):
            chunk_name = f"{document_name} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else document_name

            # Build message content (text + images)
            content = [
                {
                    "type": "text",
                    "text": f"Document: {chunk_name}\n\n{chunk}",
                }
            ]

            # Add images for this chunk (headers/footers might contain IDs)
            for img_path in chunk_images[i]:
                if img_path.exists():
                    try:
                        img_data = base64.standard_b64encode(img_path.read_bytes()).decode("utf-8")
                        media_type = "image/jpeg"
                        if img_path.suffix.lower() in [".png"]:
                            media_type = "image/png"
                        elif img_path.suffix.lower() in [".webp"]:
                            media_type = "image/webp"

                        content.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": img_data,
                                },
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to load image {img_path}: {e}")

            # Call Anthropic API with retry logic and prompt caching
            try:
                start_time = time.time()
                response = await self._call_api_with_retry(
                    self.client.messages.create,
                    model=settings.llm_model,
                    max_tokens=settings.llm_max_tokens,
                    temperature=settings.llm_temperature,
                    system=[
                        {"type": "text", "text": PROJECT_ID_EXTRACTION_PROMPT, "cache_control": {"type": "ephemeral"}}
                    ],
                    messages=[{"role": "user", "content": content}],
                    timeout=settings.api_call_timeout_seconds,
                )
                duration = time.time() - start_time

                # Track cost if tracker is enabled
                _track_api_call(
                    model=settings.llm_model,
                    extractor="project_id",
                    document_name=chunk_name,
                    usage=response.usage.model_dump() if hasattr(response, "usage") else {},
                    duration=duration,
                    cached=False,
                )

                # Parse response
                response_text = response.content[0].text

                # Extract and validate JSON from response
                json_str = extract_json_from_response(response_text)
                extracted_data = validate_and_parse_extraction_response(json_str, "project_id")

                # Filter out invalid project IDs (standalone numbers, filename prefixes)
                filtered_data = _filter_invalid_project_ids(extracted_data)

                # Convert to ExtractedField objects
                chunk_fields = [ExtractedField(**data) for data in filtered_data]
                all_fields.extend(chunk_fields)

                if len(extracted_data) > len(filtered_data):
                    logger.info(
                        f"Filtered {len(extracted_data) - len(filtered_data)} invalid project IDs "
                        f"(standalone numbers or filename prefixes)"
                    )

                logger.info(f"Extracted {len(chunk_fields)} project IDs from {chunk_name}")

            except ValueError as e:
                # Invalid JSON or structure - log and continue
                logger.error(f"Invalid response from LLM for {chunk_name}: {e}")
            except Exception as e:
                logger.error(f"Project ID extraction failed for {chunk_name}: {e}", exc_info=True)

        # Deduplicate fields (same value, keep highest confidence)
        # For project IDs, value is the key (no field_type needed since all are "project_id")
        deduplicated = {}
        for field in all_fields:
            key = field.value
            if key not in deduplicated or field.confidence > deduplicated[key].confidence:
                deduplicated[key] = field

        fields = list(deduplicated.values())

        # Cache results
        self.cache.set(cache_key, [f.model_dump() for f in fields])

        logger.info(
            f"Extracted {len(fields)} unique project IDs from {document_name} ({len(all_fields)} total before dedup)"
        )
        return fields


# Helper functions


def _filter_invalid_project_ids(extracted_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Filter out invalid project IDs that are actually document prefixes or standalone numbers.

    Args:
        extracted_data: List of extracted project ID dicts

    Returns:
        Filtered list with invalid IDs removed
    """
    import re

    filtered = []

    for item in extracted_data:
        value = str(item.get("value", "")).strip()
        raw_text = item.get("raw_text", "")

        # Skip empty values
        if not value:
            continue

        # Skip standalone numbers (4997, 4998, etc.)
        if re.match(r"^\d+$", value):
            logger.debug(f"Filtering standalone number: {value}")
            continue

        # Skip if it appears to be a document filename prefix
        # Pattern: ####ProjectYY or ####Project (e.g., 4997Botany22)
        if re.match(r"^\d{4}[A-Za-z]+\d{2}$", value):
            logger.debug(f"Filtering document filename prefix: {value}")
            continue

        # Skip if raw_text suggests this is from a filename context
        if raw_text and any(
            indicator in raw_text.lower()
            for indicator in [
                "documents submitted:",
                "document name:",
                "filename:",
                "_project_plan",
                "_baseline_report",
                "_monitoring_report",
                ".pdf",
                ".shp",
            ]
        ):
            logger.debug(f"Filtering ID from filename context: {value}")
            continue

        # Keep if it matches a known registry pattern or appears as "Project ID:"
        # Known patterns: C##-####, VCS####, GS####, CAR####, ACR####
        known_pattern = re.match(r"^(C\d{2}-\d+|VCS-?\d+|GS-?\d+|CAR\d+|ACR\d+)$", value)
        project_id_context = raw_text and "project id" in raw_text.lower()

        if known_pattern or project_id_context:
            filtered.append(item)
        else:
            logger.debug(f"Filtering ID without valid pattern or context: {value}")

    return filtered


def extract_json_from_response(response_text: str) -> str:
    """Extract JSON from LLM response that may be wrapped in markdown.

    Args:
        response_text: Raw LLM response

    Returns:
        Extracted JSON string
    """
    # Try to find JSON in markdown code blocks
    if "```json" in response_text:
        json_str = response_text.split("```json")[1].split("```")[0].strip()
    elif "```" in response_text:
        json_str = response_text.split("```")[1].split("```")[0].strip()
    else:
        json_str = response_text.strip()

    return json_str


def validate_and_parse_extraction_response(json_str: str, extractor_type: str) -> list[dict[str, Any]]:
    """Validate and parse JSON extraction response.

    Args:
        json_str: JSON string to parse
        extractor_type: Type of extractor ("date", "tenure", "project_id")

    Returns:
        List of validated extraction dictionaries

    Raises:
        ValueError: If JSON is invalid or doesn't match expected structure
    """
    # Parse JSON
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON response from {extractor_type} extractor: {e}")

    # Validate structure is a list
    if not isinstance(data, list):
        raise ValueError(f"Expected list of extractions from {extractor_type} extractor, got {type(data).__name__}")

    # Validate each extraction has required fields
    required_fields = {"value", "field_type", "confidence", "source", "reasoning"}
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(
                f"Extraction {i} from {extractor_type} extractor is not a dictionary: {type(item).__name__}"
            )

        missing_fields = required_fields - set(item.keys())
        if missing_fields:
            raise ValueError(
                f"Extraction {i} from {extractor_type} extractor missing required fields: {missing_fields}"
            )

        # Validate confidence is a number between 0 and 1
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
            raise ValueError(
                f"Extraction {i} from {extractor_type} extractor has invalid confidence: {confidence} "
                f"(must be number between 0.0 and 1.0)"
            )

    return data


def extract_doc_id(source: str) -> str | None:
    """Extract document ID from source string.

    Examples:
        "Project Plan, Section 1.8, Page 4" -> None
        "DOC-001, Page 5" -> "DOC-001"
        "REQ-002" -> "REQ-002"
        "C06-REGISTRATION-ML-014" -> "C06-REGISTRATION-ML-014"
    """
    match = re.search(
        r"(DOC-[A-Za-z0-9]+|C\d{2}-[A-Z]+-[A-Z]{2}-\d{3}|REQ-\d+)",
        source,
    )
    return match.group(1) if match else None


def extract_doc_name(source: str) -> str:
    """Extract document name from source string.

    Examples:
        "Project Plan, Section 1.8, Page 4" -> "Project Plan"
        "DOC-001, Page 5" -> "DOC-001"
    """
    return source.split(",")[0].strip()


def extract_page(source: str) -> int | None:
    """Extract page number from source string.

    Examples:
        "Project Plan, Section 1.8, Page 4" -> 4
        "No page mentioned" -> None
    """
    match = re.search(r"Page (\d+)", source, re.IGNORECASE)
    return int(match.group(1)) if match else None


def group_fields_by_document(fields: list[ExtractedField]) -> list[dict]:
    """Group tenure fields by document into validation-ready format.

    Args:
        fields: List of extracted fields (owner_name, area_hectares, tenure_type)

    Returns:
        List of grouped dicts with all fields from same document
    """
    by_doc: dict[str, dict] = {}

    for field in fields:
        # Use document name or ID as key
        doc_key = extract_doc_id(field.source) or extract_doc_name(field.source)

        if doc_key not in by_doc:
            by_doc[doc_key] = {
                "document_id": extract_doc_id(field.source),
                "document_name": extract_doc_name(field.source),
                "page": extract_page(field.source),
                "source": field.source,
                "confidence": field.confidence,
            }

        # Add field value to appropriate key
        if field.field_type == "owner_name":
            by_doc[doc_key]["owner_name"] = field.value
        elif field.field_type == "area_hectares":
            by_doc[doc_key]["area_hectares"] = field.value
        elif field.field_type == "tenure_type":
            by_doc[doc_key]["tenure_type"] = field.value
        elif field.field_type == "ownership_percentage":
            by_doc[doc_key]["ownership_percentage"] = field.value

    return list(by_doc.values())


async def extract_fields_with_llm(session_id: str, evidence_data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract structured fields from evidence using LLM.

    Main entry point called by cross_validate().

    Args:
        session_id: Session identifier
        evidence_data: Evidence JSON with snippets

    Returns:
        Dictionary with extracted fields:
        {
            "dates": [ExtractedField, ...],
            "tenure": [ExtractedField, ...],
            "project_ids": [ExtractedField, ...]
        }
    """
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not set - required for LLM extraction")

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    date_extractor = DateExtractor(client)
    tenure_extractor = LandTenureExtractor(client)
    project_id_extractor = ProjectIDExtractor(client)

    all_dates = []
    all_tenure = []
    all_project_ids = []

    # Extract from evidence snippets (token-efficient)
    for req in evidence_data.get("evidence", []):
        req_id = req["requirement_id"]

        # Process date-related requirements
        if req_id in ["REQ-007", "REQ-018", "REQ-019"]:
            # Aggregate snippets for this requirement
            snippet_texts = []
            for snippet in req.get("evidence_snippets", [])[:5]:  # Limit to 5 snippets
                snippet_texts.append(f"[{snippet['document_name']}]\n{snippet['text']}")

            if snippet_texts:
                markdown = "\n\n---\n\n".join(snippet_texts)
                dates = await date_extractor.extract(markdown, [], req_id)
                all_dates.extend(dates)

        # Process land tenure requirements
        elif req_id in ["REQ-003", "REQ-004"]:  # Land tenure/ownership requirements
            snippet_texts = []
            for snippet in req.get("evidence_snippets", [])[:5]:
                snippet_texts.append(f"[{snippet['document_name']}]\n{snippet['text']}")

            if snippet_texts:
                markdown = "\n\n---\n\n".join(snippet_texts)
                tenure_fields = await tenure_extractor.extract(markdown, [], req_id)
                all_tenure.extend(tenure_fields)

        # Process project ID requirements (extract from all requirements to find IDs)
        # Project IDs can appear anywhere in the documents
        if req_id in ["REQ-001", "REQ-002"]:  # Project identification requirements
            snippet_texts = []
            for snippet in req.get("evidence_snippets", [])[:5]:
                snippet_texts.append(f"[{snippet['document_name']}]\n{snippet['text']}")

            if snippet_texts:
                markdown = "\n\n---\n\n".join(snippet_texts)
                project_ids = await project_id_extractor.extract(markdown, [], req_id)
                all_project_ids.extend(project_ids)

    return {
        "dates": all_dates,
        "tenure": all_tenure,
        "project_ids": all_project_ids,
    }
