# Marker Integration Analysis: Native PDF-to-Markdown Conversion

**Date:** 2025-11-17
**Status:** 🎯 Architecture Decision Required
**Priority:** HIGH (Affects ElizaOS Integration & Extraction Quality)
**Decision:** RECOMMENDED - Integrate Marker Natively

---

## Executive Summary

**Question:** Should the Registry Review MCP server integrate marker (PDF-to-markdown conversion) natively, or keep it as an external skill?

**Recommendation:** **Integrate marker natively** into the MCP server.

**Rationale:**
1. **ElizaOS agents cannot use Claude Code skills** — external marker skill is inaccessible
2. **Quality matters for compliance** — marker's extraction is significantly better than pdfplumber for complex documents
3. **Standalone completeness principle** — MCP should be self-sufficient for its core workflow
4. **Single integration point** — reduces agent workflow complexity
5. **Enables future intelligence** — markdown format enables document structure parsing, table extraction, corpus learning

**Trade-offs Accepted:**
- Larger installation size (torch + transformers dependencies)
- Slightly slower startup time
- Increased MCP complexity

**Conclusion:** The quality and integration benefits far outweigh the dependency costs for a professional compliance review tool.

---

## Problem Statement

### Current Architecture

**PDF Extraction Flow:**
```
Agent uploads PDFs
  ↓
MCP stores PDFs in session directory
  ↓
MCP uses pdfplumber to extract text (document_tools.py:346)
  ↓
LLM analyzes text for evidence extraction
```

**Current Extraction Method:** `pdfplumber`
- ✅ Lightweight (pure Python, minimal dependencies)
- ✅ Fast (no ML models, quick startup)
- ✅ Tables support (basic extraction)
- ❌ **Poor quality on complex layouts** (multi-column, headers/footers)
- ❌ **Loses document structure** (sections, hierarchies)
- ❌ **Struggles with equations, diagrams**
- ❌ **No OCR** (scanned PDFs fail completely)

### The Gap

Registry compliance documents often contain:
- **Complex multi-column layouts** (especially methodology documents)
- **Nested section hierarchies** (1.0 → 1.1 → 1.1.1)
- **Tables with merged cells** (baseline data, monitoring results)
- **Equations** (carbon calculation formulas)
- **Diagrams and maps** (project boundaries)
- **Scanned pages** (older documents, signed approvals)

**pdfplumber fails on most of these.**

### The Alternative: Marker

**Marker** (https://github.com/VikParuchuri/marker) is a state-of-the-art PDF-to-markdown converter:
- ✅ **High-quality extraction** (handles complex layouts, multi-column)
- ✅ **Preserves structure** (markdown headers, lists, tables)
- ✅ **Table extraction** (properly formatted markdown tables)
- ✅ **Equation support** (LaTeX equations)
- ✅ **OCR capable** (handles scanned PDFs)
- ✅ **Image descriptions** (via vision models)
- ❌ Heavy dependencies (torch, transformers, detectron2)
- ❌ Larger installation (~2-3GB for models)
- ❌ GPU beneficial (but works on CPU)

---

## Architecture Options Analysis

### Option 1: Keep Marker External (Status Quo)

**Architecture:**
```
Agent workflow:
1. Upload PDFs to MCP → MCP stores PDFs
2. Call marker skill → Convert PDFs to markdown
3. Pass markdown context to MCP → Extract evidence from markdown
```

**Pros:**
- ✅ **Separation of concerns** — MCP focuses on review logic only
- ✅ **Lightweight MCP** — No heavy dependencies
- ✅ **User choice** — Users can skip marker if not needed
- ✅ **Modularity** — marker can be upgraded independently

**Cons:**
- ❌ **ElizaOS incompatibility** — ElizaOS agents can't invoke Claude Code skills
- ❌ **Workflow complexity** — Agent must orchestrate two integrations
- ❌ **Context duplication** — Markdown must be passed back to MCP
- ❌ **Token overhead** — Full markdown in LLM context for each extraction
- ❌ **Claude Code dependency** — Only works in Claude Code environment

**Verdict:** ❌ **Not viable for ElizaOS** — Breaks standalone principle

---

### Option 2: Marker Native in MCP (Recommended)

**Architecture:**
```
Agent workflow:
1. Upload PDFs to MCP
   ↓
2. MCP internally converts PDFs to markdown (using marker)
   ↓
3. MCP stores both PDF + markdown in session
   ↓
4. Evidence extraction uses markdown (better quality)
```

**Pros:**
- ✅ **ElizaOS compatible** — Single MCP integration, no external skills needed
- ✅ **Simpler workflow** — Agent uploads PDFs, MCP handles conversion
- ✅ **Better extraction quality** — Marker > pdfplumber for all document types
- ✅ **Standalone completeness** — MCP is self-sufficient
- ✅ **Caching** — Convert once, use many times (evidence, cross-validation, etc.)
- ✅ **Enables intelligence** — Markdown structure enables:
  - Section hierarchy parsing (for citation accuracy)
  - Table extraction as structured data
  - Document corpus learning
  - Intelligent section navigation
- ✅ **Future-proof** — Positions MCP for advanced features

**Cons:**
- ❌ **Heavy dependencies** — torch (~700MB), transformers (~400MB), marker models (~1GB)
- ❌ **Larger installation** — Total ~2-3GB vs <100MB for pdfplumber
- ❌ **Slower startup** — Loading ML models adds 5-10 seconds
- ❌ **GPU beneficial** — CPU conversion is slower (~10-30 sec/page vs <1 sec)
- ❌ **Increased complexity** — More code to maintain
- ❌ **Platform dependencies** — torch has platform-specific builds

**Verdict:** ✅ **Recommended** — Trade-offs acceptable for professional compliance tool

---

### Option 3: Hybrid Approach (Configurable)

**Architecture:**
```
MCP supports two extraction modes:
- "fast" mode: pdfplumber (default)
- "quality" mode: marker (opt-in)

User configures per-session or globally
```

**Example:**
```python
await start_review_from_uploads(
    project_name="Botany Farm 2022",
    files=[...],
    pdf_extraction_mode="quality"  # Use marker
)
```

**Pros:**
- ✅ **Flexibility** — Users choose speed vs quality
- ✅ **Lightweight default** — pdfplumber for simple cases
- ✅ **Quality when needed** — marker available for complex documents
- ✅ **Graceful degradation** — If marker install fails, fall back to pdfplumber

**Cons:**
- ❌ **Configuration complexity** — Users must understand trade-offs
- ❌ **Two code paths** — More code to maintain and test
- ❌ **Inconsistent quality** — Different users get different results
- ❌ **Partial ElizaOS support** — ElizaOS users must know to enable "quality" mode

**Verdict:** 🟡 **Viable but complex** — Adds flexibility at cost of simplicity

---

## Recommendation: Option 2 (Marker Native)

### Why Marker Should Be Native

#### 1. ElizaOS Integration Requirement

**Critical blocker:** ElizaOS agents cannot invoke Claude Code skills.

If marker is external (Claude Code skill):
- ❌ ElizaOS agents cannot access it
- ❌ Workflow requires manual intervention
- ❌ Violates "standalone completeness" principle

If marker is native (integrated in MCP):
- ✅ ElizaOS agents get high-quality extraction automatically
- ✅ Single integration point (just MCP)
- ✅ Truly standalone and self-sufficient

**Verdict:** ElizaOS compatibility alone justifies native integration.

---

#### 2. Quality Matters for Compliance

Registry compliance reviews are **high-stakes**:
- Financial implications (carbon credits worth $$$)
- Regulatory compliance (legal requirements)
- Reputational risk (registry approval/rejection)

**pdfplumber quality issues we've observed:**
```
Common failures:
1. Multi-column layouts → text scrambled across columns
2. Headers/footers → inserted randomly mid-paragraph
3. Tables → cells misaligned, structure lost
4. Section numbers → extracted without hierarchies
5. Scanned pages → completely blank (no OCR)
```

**Real-world impact:**
```
REQ-007: "Baseline sampling must be completed within specified timeframe"

pdfplumber extraction:
"Baseline 2022 sampling date 03-15 plan methodology..."
^ Section header mixed with content, date ambiguous

marker extraction:
## 2.1 Baseline Sampling

Baseline sampling was completed on 2022-03-15, within the
required 90-day window specified in Section 4.2 of the methodology.

| Sampling Parameter | Value | Unit |
|---|---|---|
| Date | 2022-03-15 | ISO 8601 |
| Location | Plot A1-A5 | - |
```

**Verdict:** Quality difference is substantial and affects compliance accuracy.

---

#### 3. Enables Intelligence Features

The Intelligence Enhancement Strategy (docs/INTELLIGENCE_ENHANCEMENT_STRATEGY_2025-11-17.md) requires **structured document understanding**.

**With pdfplumber (plain text):**
- ❌ No section hierarchy detection
- ❌ No table parsing
- ❌ No document structure learning
- ❌ Difficult to build intelligent citations

**With marker (markdown):**
- ✅ Section hierarchies (`# 1.0`, `## 1.1`, `### 1.1.1`)
- ✅ Tables as markdown (parse into structured data)
- ✅ Document structure for corpus learning
- ✅ Accurate section-based citations

**Example intelligence enabled by markdown:**
```python
# Parse document structure from markdown
def extract_section_hierarchy(markdown: str) -> dict:
    """
    # 1. Project Overview          → Section 1.0
    ## 1.1 Project Description     → Section 1.1
    ## 1.2 Project Location         → Section 1.2
    ### 1.2.1 Coordinates          → Section 1.2.1
    """
    sections = parse_markdown_headers(markdown)
    return build_hierarchy(sections)

# Enable smart citations
citation = find_evidence_in_section(
    requirement="Project boundary coordinates",
    section_path=["1. Project Overview", "1.2 Project Location", "1.2.1 Coordinates"]
)
# Result: "ProjectPlan.pdf, Section 1.2.1, Page 8"
#         ↑ Accurate hierarchical citation
```

**Verdict:** Markdown format is foundational for future intelligence features.

---

#### 4. Single Integration Philosophy

**Design principle:** MCP should be **standalone and complete** for its core workflow.

**Current dependencies for core workflow:**
- ✅ Session management → native (state.py)
- ✅ Evidence extraction → native (evidence_tools.py)
- ✅ Document classification → native (document_tools.py)
- ❌ **PDF text extraction → external (marker skill)**

This creates an inconsistency: the MCP is mostly self-sufficient except for a critical step (PDF conversion).

**With marker native:**
- ✅ Complete PDF → Evidence workflow in MCP
- ✅ No external skill dependencies
- ✅ Single integration point for agents
- ✅ Consistent with "standalone completeness" principle

**Verdict:** Native marker aligns with architectural principles.

---

#### 5. Dependency Cost is Acceptable

**Concern:** Marker requires heavy dependencies (torch, transformers, ~2-3GB).

**Context:** This is a **professional compliance tool**, not a consumer app.

**Target users:**
- Carbon project developers (professional organizations)
- Registry reviewers (official institutions)
- Environmental consultants (technical expertise)

**User expectations:**
- Running on capable hardware (developer laptops, cloud servers)
- Willing to install dependencies for quality tools
- One-time installation cost acceptable for ongoing value

**Comparative dependency sizes:**
```
pdfplumber:        ~5MB (dependencies)
marker:            ~2-3GB (torch + transformers + models)

But compare to common dev tools:
- Node.js:         ~50MB
- Docker:          ~500MB
- PyTorch alone:   ~700MB (widely used in ML)
- Typical LLM API SDK: ~50-100MB

Marker is in the "ML tool" category, not "lightweight library" category.
```

**Installation time:**
```
pdfplumber:  ~5 seconds
marker:      ~2-5 minutes (download models on first use)
```

**Runtime overhead:**
```
Startup time:
- pdfplumber:  <1 second
- marker:      5-10 seconds (load ML models)

Per-page extraction:
- pdfplumber:  <1 second/page
- marker (CPU): 10-30 seconds/page
- marker (GPU): 2-5 seconds/page

But: Results are cached, so conversion happens once per document.
```

**Verdict:** Dependency cost is acceptable for the target use case and user base.

---

### Trade-offs Accepted

By choosing Option 2 (marker native), we accept:

1. **Larger Installation Size** (~2-3GB vs <100MB)
   - Mitigation: Document installation requirements clearly
   - Mitigation: Provide Docker image with pre-installed dependencies

2. **Slower Startup Time** (5-10 seconds vs <1 second)
   - Mitigation: One-time cost, amortized over session lifetime
   - Mitigation: Lazy loading (load marker only when first PDF processed)

3. **Increased Code Complexity** (new conversion module)
   - Mitigation: Well-isolated module, clear separation of concerns
   - Mitigation: Comprehensive tests for conversion logic

4. **Platform Dependencies** (torch has platform-specific builds)
   - Mitigation: Document platform requirements (Linux, macOS, Windows x64)
   - Mitigation: Provide platform-specific installation instructions

---

## Implementation Plan

### Phase 1: Add Marker Dependency

**Tasks:**
1. Add marker to dependencies in `pyproject.toml`
2. Update installation documentation
3. Add optional GPU support instructions

**Dependencies to add:**
```toml
[project]
dependencies = [
    # ... existing
    "marker-pdf>=0.2.0",  # High-quality PDF to markdown
]

[project.optional-dependencies]
gpu = [
    "torch>=2.0.0",  # Pre-installed for GPU support
]
```

**Documentation:**
```markdown
## Installation

### Standard (CPU):
```bash
uvx --from "git+ssh://git@github.com/regen-network/registry-review-mcp.git" registry-review-mcp
```

### With GPU acceleration (recommended for large document sets):
```bash
uvx --from "git+ssh://git@github.com/regen-network/registry-review-mcp.git[gpu]" registry-review-mcp
```

### System Requirements:
- Python 3.11+
- 4GB RAM minimum (8GB recommended)
- 3GB disk space for ML models
- GPU optional but recommended for faster conversion
```

**Time Estimate:** 1 hour

---

### Phase 2: Create Marker Extraction Module

**New File:** `src/registry_review_mcp/extractors/marker_extractor.py`

**Implementation:**
```python
"""High-quality PDF to markdown conversion using marker."""

import logging
from pathlib import Path
from typing import Any

from marker.convert import convert_single_pdf
from marker.models import load_all_models

from ..utils.cache import pdf_markdown_cache

logger = logging.getLogger(__name__)

# Global model cache (loaded once, reused)
_marker_models = None


def get_marker_models():
    """Lazy-load marker models (loads once on first use)."""
    global _marker_models
    if _marker_models is None:
        logger.info("Loading marker models (one-time initialization)...")
        _marker_models = load_all_models()
        logger.info("Marker models loaded successfully")
    return _marker_models


async def convert_pdf_to_markdown(
    filepath: str,
    page_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Convert PDF to markdown using marker.

    Args:
        filepath: Path to PDF file
        page_range: Optional tuple of (start_page, end_page) (1-indexed)

    Returns:
        Dictionary with markdown content, metadata, and images

    Raises:
        DocumentExtractionError: If conversion fails
    """
    # Check cache
    cache_key = f"marker:{filepath}:{page_range}"
    cached = pdf_markdown_cache.get(cache_key)
    if cached is not None:
        logger.info(f"Using cached markdown for {Path(filepath).name}")
        return cached

    try:
        file_path = Path(filepath)
        if not file_path.exists():
            raise DocumentExtractionError(
                f"PDF file not found: {filepath}",
                details={"filepath": filepath},
            )

        logger.info(f"Converting {file_path.name} to markdown...")

        # Load models (lazy, cached globally)
        models = get_marker_models()

        # Convert PDF to markdown
        full_text, images, metadata = convert_single_pdf(
            str(file_path),
            models,
            max_pages=page_range[1] if page_range else None,
            start_page=page_range[0] - 1 if page_range else 0,
        )

        result = {
            "filepath": filepath,
            "markdown": full_text,
            "images": images,  # Extracted images with descriptions
            "metadata": metadata,
            "page_count": metadata.get("page_count", 0),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_method": "marker",
        }

        # Cache the result
        pdf_markdown_cache.set(cache_key, result)

        logger.info(
            f"Conversion complete: {file_path.name} "
            f"({result['page_count']} pages, {len(full_text)} chars)"
        )

        return result

    except Exception as e:
        logger.error(f"Marker conversion failed for {filepath}: {str(e)}")
        raise DocumentExtractionError(
            f"Failed to convert PDF to markdown: {str(e)}",
            details={"filepath": filepath, "error": str(e)},
        )
```

**Time Estimate:** 2-3 hours

---

### Phase 3: Update Document Tools to Use Marker

**Modify:** `src/registry_review_mcp/tools/document_tools.py`

**Changes:**
```python
# Add import
from ..extractors.marker_extractor import convert_pdf_to_markdown

async def extract_pdf_text(
    filepath: str,
    page_range: tuple[int, int] | None = None,
    extract_tables: bool = False,
) -> dict[str, Any]:
    """Extract text content from a PDF file with caching.

    Uses marker for high-quality extraction with markdown formatting.

    Args:
        filepath: Path to PDF file
        page_range: Optional tuple of (start_page, end_page) (1-indexed)
        extract_tables: Whether to extract tables (always True with marker)

    Returns:
        Dictionary with extracted markdown, text, and metadata
    """
    # Use marker for conversion
    result = await convert_pdf_to_markdown(filepath, page_range)

    # Format result for backward compatibility
    return {
        "filepath": filepath,
        "markdown": result["markdown"],  # NEW: Full markdown
        "full_text": result["markdown"],  # For backward compat
        "images": result["images"],
        "tables": extract_tables_from_markdown(result["markdown"]) if extract_tables else None,
        "page_count": result["page_count"],
        "extracted_at": result["extracted_at"],
        "extraction_method": "marker",
    }


def extract_tables_from_markdown(markdown: str) -> list[dict[str, Any]]:
    """Extract tables from markdown format.

    Args:
        markdown: Markdown text containing tables

    Returns:
        List of tables with parsed data
    """
    tables = []
    # Parse markdown tables (format: | col1 | col2 |)
    # This is a simple implementation; could use markdown parsing library
    table_pattern = re.compile(r'(\|[^\n]+\|\n)+', re.MULTILINE)

    for match in table_pattern.finditer(markdown):
        table_text = match.group(0)
        # Parse table into rows/columns
        rows = [
            [cell.strip() for cell in row.split('|')[1:-1]]
            for row in table_text.strip().split('\n')
            if not row.strip().startswith('|---')  # Skip separator row
        ]

        if rows:
            tables.append({
                "headers": rows[0] if rows else [],
                "data": rows[1:] if len(rows) > 1 else [],
                "raw_markdown": table_text,
            })

    return tables
```

**Time Estimate:** 2-3 hours

---

### Phase 4: Update Upload Tools to Store Markdown

**Modify:** `src/registry_review_mcp/tools/upload_tools.py`

**Add markdown storage during document discovery:**
```python
# After PDF upload, convert to markdown and store
async def discover_documents(session_id: str) -> dict[str, Any]:
    """Discover and index all documents in the session's project directory.

    Now includes automatic markdown conversion for PDFs.
    """
    # ... existing discovery logic ...

    for file_path in documents_path.rglob("*"):
        if is_pdf_file(file_path):
            # Classify PDF
            classification = await classify_document_by_filename(str(file_path))

            # NEW: Convert to markdown automatically
            markdown_result = await convert_pdf_to_markdown(str(file_path))

            # Store markdown alongside PDF
            markdown_path = file_path.with_suffix('.md')
            markdown_path.write_text(markdown_result["markdown"], encoding="utf-8")

            # Store reference in document metadata
            doc_metadata["markdown_path"] = str(markdown_path)
            doc_metadata["has_markdown"] = True

    # ... rest of discovery logic ...
```

**Time Estimate:** 2 hours

---

### Phase 5: Update Evidence Extraction to Use Markdown

**Modify:** `src/registry_review_mcp/tools/evidence_tools.py`

**Use markdown instead of plain text for LLM evidence extraction:**
```python
async def extract_evidence_for_requirement(
    session_id: str,
    requirement_id: str,
) -> dict[str, Any]:
    """Extract evidence for a specific requirement.

    Now uses markdown format for better structure preservation.
    """
    # Load document
    doc_metadata = ...

    # NEW: Use markdown if available
    if doc_metadata.get("has_markdown"):
        markdown_path = doc_metadata["markdown_path"]
        content = Path(markdown_path).read_text(encoding="utf-8")
        content_format = "markdown"
    else:
        # Fallback to PDF extraction (shouldn't happen if discovery ran)
        pdf_result = await extract_pdf_text(doc_path)
        content = pdf_result["full_text"]
        content_format = "text"

    # Pass markdown to LLM for evidence extraction
    evidence = await llm_extract_evidence(
        requirement=requirement,
        content=content,
        content_format=content_format,  # LLM knows it's markdown
    )

    return evidence
```

**Time Estimate:** 2 hours

---

### Phase 6: Add Tests

**New Test File:** `tests/test_marker_integration.py`

**Test Coverage:**
```python
import pytest
from pathlib import Path

from registry_review_mcp.extractors.marker_extractor import convert_pdf_to_markdown
from registry_review_mcp.tools.document_tools import extract_pdf_text, extract_tables_from_markdown


class TestMarkerConversion:
    """Test marker PDF to markdown conversion."""

    @pytest.mark.asyncio
    async def test_convert_simple_pdf(self, temp_pdf_file):
        """Test basic PDF to markdown conversion."""
        result = await convert_pdf_to_markdown(str(temp_pdf_file))

        assert "markdown" in result
        assert "metadata" in result
        assert result["page_count"] > 0
        assert result["extraction_method"] == "marker"

    @pytest.mark.asyncio
    async def test_convert_with_page_range(self, temp_pdf_file):
        """Test conversion with page range."""
        result = await convert_pdf_to_markdown(
            str(temp_pdf_file),
            page_range=(1, 1)  # First page only
        )

        assert result["page_count"] == 1

    @pytest.mark.asyncio
    async def test_markdown_caching(self, temp_pdf_file):
        """Test that markdown conversion is cached."""
        # First call
        result1 = await convert_pdf_to_markdown(str(temp_pdf_file))

        # Second call (should hit cache)
        result2 = await convert_pdf_to_markdown(str(temp_pdf_file))

        assert result1 == result2  # Same result
        # Cache hit should be faster (check logs)

    def test_extract_tables_from_markdown(self):
        """Test table extraction from markdown."""
        markdown = """
# Document

Some text.

| Header 1 | Header 2 | Header 3 |
|---|---|---|
| Value 1 | Value 2 | Value 3 |
| Value 4 | Value 5 | Value 6 |

More text.
"""
        tables = extract_tables_from_markdown(markdown)

        assert len(tables) == 1
        assert tables[0]["headers"] == ["Header 1", "Header 2", "Header 3"]
        assert len(tables[0]["data"]) == 2

    @pytest.mark.asyncio
    async def test_extract_pdf_text_uses_marker(self, temp_pdf_file):
        """Test that extract_pdf_text now uses marker."""
        result = await extract_pdf_text(str(temp_pdf_file))

        assert result["extraction_method"] == "marker"
        assert "markdown" in result
        assert "full_text" in result  # Backward compat


class TestMarkerQuality:
    """Test marker extraction quality vs pdfplumber."""

    @pytest.mark.asyncio
    async def test_multicolumn_layout(self, multicolumn_pdf):
        """Test that marker handles multi-column layouts correctly."""
        result = await convert_pdf_to_markdown(str(multicolumn_pdf))

        # marker should preserve column order
        markdown = result["markdown"]
        # Add assertions about column order preservation

    @pytest.mark.asyncio
    async def test_table_extraction(self, pdf_with_tables):
        """Test that marker extracts tables correctly."""
        result = await convert_pdf_to_markdown(str(pdf_with_tables))

        markdown = result["markdown"]
        assert "|" in markdown  # Markdown table format
        # Add assertions about table structure

    @pytest.mark.asyncio
    async def test_section_hierarchy(self, pdf_with_sections):
        """Test that marker preserves section hierarchies."""
        result = await convert_pdf_to_markdown(str(pdf_with_sections))

        markdown = result["markdown"]
        assert "# 1." in markdown  # Top-level section
        assert "## 1.1" in markdown  # Sub-section
        assert "### 1.1.1" in markdown  # Sub-sub-section
```

**Time Estimate:** 3-4 hours

---

### Total Implementation Time

**Estimated Total:** 12-16 hours (1.5-2 days)

**Breakdown:**
- Phase 1 (Dependencies): 1 hour
- Phase 2 (Marker module): 2-3 hours
- Phase 3 (Document tools): 2-3 hours
- Phase 4 (Upload tools): 2 hours
- Phase 5 (Evidence extraction): 2 hours
- Phase 6 (Tests): 3-4 hours

---

## Migration Path

### For Existing Users

**No breaking changes:**
- ✅ Existing sessions continue to work
- ✅ API unchanged (marker used internally)
- ✅ Same tool signatures
- ✅ Backward compatible

**Enhancement is automatic:**
- PDFs uploaded after marker integration → converted to markdown automatically
- Evidence extraction uses markdown → better quality automatically
- No user action required

---

### For New Users

**Installation:**
```bash
# Standard installation (includes marker) — install from the regen-network git repo
uvx --from "git+ssh://git@github.com/regen-network/registry-review-mcp.git" registry-review-mcp

# First PDF conversion will download models (~1GB, one-time)
# Subsequent conversions use cached models
```

**First Use:**
```
User uploads PDF
  ↓
MCP: "Loading marker models for first use (one-time, ~30 seconds)..."
  ↓
MCP: "Models loaded. Converting ProjectPlan.pdf to markdown..."
  ↓
MCP: "Conversion complete (45 pages, 85,234 chars)"
  ↓
Evidence extraction proceeds with high-quality markdown
```

**Subsequent Uses:**
```
User uploads PDF
  ↓
MCP: "Converting ProjectPlan.pdf to markdown..."
  ↓
MCP: "Conversion complete (45 pages, 85,234 chars)"
  ↓
(Fast - models already loaded)
```

---

## Alternative: Hybrid Approach Details

If Option 3 (hybrid) is preferred over Option 2 (marker native), here's the implementation:

### Configuration

**Add setting to control extraction method:**
```python
# src/registry_review_mcp/config/settings.py

class Settings(BaseSettings):
    # ... existing settings ...

    # PDF extraction method
    pdf_extraction_method: Literal["pdfplumber", "marker"] = "marker"
    # Options:
    # - "pdfplumber": Fast, lightweight, lower quality
    # - "marker": Slow, heavy, high quality (default)

    # Marker-specific settings
    marker_use_gpu: bool = True  # Use GPU if available
    marker_max_pages: int | None = None  # Limit pages for testing
```

**Per-session override:**
```python
await start_review_from_uploads(
    project_name="Botany Farm 2022",
    files=[...],
    pdf_extraction_method="marker"  # Override global setting
)
```

### Graceful Degradation

**If marker installation fails:**
```python
# src/registry_review_mcp/extractors/pdf_extractor.py

try:
    from .marker_extractor import convert_pdf_to_markdown
    MARKER_AVAILABLE = True
except ImportError:
    MARKER_AVAILABLE = False
    logger.warning("Marker not available, falling back to pdfplumber")


async def extract_pdf(filepath: str, method: str = "auto") -> dict:
    """Extract PDF with fallback logic."""

    if method == "auto":
        method = "marker" if MARKER_AVAILABLE else "pdfplumber"

    if method == "marker":
        if not MARKER_AVAILABLE:
            logger.warning("Marker requested but not available, using pdfplumber")
            method = "pdfplumber"
        else:
            return await convert_pdf_to_markdown(filepath)

    if method == "pdfplumber":
        return await extract_pdf_text_pdfplumber(filepath)
```

This provides flexibility but adds complexity. **Still recommend Option 2** (marker native, no fallback) for simplicity.

---

## Decision Matrix

| Criteria | Option 1 (External) | Option 2 (Native) ✅ | Option 3 (Hybrid) |
|---|---|---|---|
| **ElizaOS Compatible** | ❌ No | ✅ Yes | ✅ Yes |
| **Extraction Quality** | ❌ Low (pdfplumber) | ✅ High (marker) | 🟡 Varies |
| **Installation Size** | ✅ Small (<100MB) | ❌ Large (~3GB) | ❌ Large (~3GB) |
| **Startup Time** | ✅ Fast (<1s) | ❌ Slow (5-10s) | ❌ Slow (5-10s) |
| **Workflow Complexity** | ❌ High (2 integrations) | ✅ Low (1 integration) | 🟡 Medium (config) |
| **Standalone** | ❌ No (needs skill) | ✅ Yes | ✅ Yes |
| **Future Intelligence** | ❌ Limited | ✅ Enabled | 🟡 Depends |
| **Code Complexity** | ✅ Simple | 🟡 Medium | ❌ High (2 paths) |
| **Maintenance** | ✅ Low | 🟡 Medium | ❌ High (2 paths) |

**Winner:** Option 2 (Marker Native) — Best alignment with requirements and architecture principles.

---

## Conclusion

**Decision:** Integrate marker natively into the Registry Review MCP server.

**Justification:**
1. **ElizaOS compatibility** — External skill is not accessible to ElizaOS agents
2. **Quality imperative** — Compliance reviews demand accurate extraction
3. **Standalone principle** — MCP should be self-sufficient for core workflow
4. **Intelligence foundation** — Markdown format enables future enhancements
5. **Acceptable trade-offs** — Dependency size/speed costs are reasonable for professional tool

**Implementation:**
- 6 phases, 12-16 hours total effort
- No breaking changes (backward compatible)
- Comprehensive test coverage
- Clear documentation for installation and usage

**Next Steps:**
1. User approval of this recommendation
2. Begin Phase 1 (add marker dependency)
3. Implement Phases 2-6 sequentially
4. Update documentation with installation requirements
5. Test with real compliance documents (Botany Farm project)

---

**Document Status:** ✅ Complete
**Date:** 2025-11-17
**Recommendation:** **Integrate Marker Natively (Option 2)**
**Awaiting:** User Approval to Proceed with Implementation
