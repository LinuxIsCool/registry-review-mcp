"""Smoke tests: Ultra-fast critical path validation.

These tests verify that core functionality works without any failures.
Target runtime: < 1 second total.

Usage:
    pytest -m smoke -v          # Run smoke tests only
    ptw -- -m smoke             # Watch mode
    make smoke                  # Via Makefile
"""


import pytest

from registry_review_mcp.extractors.verification import (
    verify_citation,
    verify_extracted_field,
)
from registry_review_mcp.utils.cache import Cache
from registry_review_mcp.utils.state import StateManager


@pytest.mark.smoke
class TestCoreInfrastructure:
    """Essential infrastructure tests that must always pass."""

    def test_settings_initialize(self, test_settings):
        """Settings load and directories exist."""
        assert test_settings.log_level in ["DEBUG", "INFO", "WARNING", "ERROR"]
        assert test_settings.data_dir.exists()
        assert test_settings.checklists_dir.exists()
        assert test_settings.sessions_dir.exists()

    def test_state_manager_write_read(self, test_settings):
        """State manager can persist data."""
        manager = StateManager("session-aa0000000001")
        test_data = {"status": "ok", "count": 42}

        manager.write_json("smoke.json", test_data)
        read_data = manager.read_json("smoke.json")

        assert read_data == test_data

    def test_cache_store_retrieve(self, test_settings):
        """Cache can store and retrieve values."""
        cache = Cache("smoke-test-cache")

        cache.set("test_key", "test_value")
        assert cache.get("test_key") == "test_value"
        assert cache.exists("test_key")


@pytest.mark.smoke
class TestCriticalValidation:
    """Core validation logic that protects data integrity."""

    def test_citation_verification_exact_match(self):
        """Citation verification detects exact matches."""
        raw_text = "Project Start Date: 01/01/2022"
        source = "The project began on January 1, 2022. Project Start Date: 01/01/2022 is when we commenced."

        is_verified, score, snippet = verify_citation(raw_text, source, "project_start_date")

        assert is_verified is True
        assert score >= 95.0

    def test_citation_verification_hallucination_detection(self):
        """Citation verification catches hallucinations."""
        raw_text = "Project ended on December 31, 2023"
        source = "Project started in January 2022"

        is_verified, score, snippet = verify_citation(raw_text, source, "project_end_date")

        assert is_verified is False
        assert score < 50.0

    def test_extracted_field_confidence_penalty(self):
        """Unverified fields get confidence penalty."""
        field = {
            "value": "2022-01-01",
            "field_type": "project_start_date",
            "confidence": 0.95,
            "raw_text": "January 1, 2022",
        }
        source_text = "The project began in March 2022"  # Different!

        verified_field = verify_extracted_field(field, source_text)

        # Confidence should be reduced
        assert verified_field["confidence"] < 0.95
        assert verified_field["verification_status"] == "failed"


@pytest.mark.smoke
@pytest.mark.asyncio
class TestSessionManagement:
    """Critical session operations."""

    async def test_create_and_load_session(self, test_settings, tmp_path, cleanup_sessions):
        """Sessions can be created and loaded."""
        from registry_review_mcp.tools import session_tools

        # Create minimal session
        result = await session_tools.create_session(
            project_name="Smoke Test Project",
            documents_path=str(tmp_path),
            methodology="soil-carbon-v1.2.2",
        )

        session_id = result["session_id"]
        assert session_id is not None

        # Load session back
        loaded = await session_tools.load_session(session_id)
        assert loaded["session_id"] == session_id
        assert loaded["project_metadata"]["project_name"] == "Smoke Test Project"


@pytest.mark.smoke
class TestDataIntegrity:
    """Essential data structures and validation."""

    def test_checklist_structure(self):
        """Soil carbon checklist is valid."""
        # Checklists are bundled inside the installed package so uvx installs
        # resolve correctly. Locate through the bundled data helper rather
        # than the legacy repo-root path.
        from registry_review_mcp.config.settings import _get_bundled_data_dir

        checklist_path = (
            _get_bundled_data_dir() / "checklists" / "soil-carbon-v1.2.2.json"
        )

        assert checklist_path.exists(), f"Checklist file missing at {checklist_path}"

        import json
        with open(checklist_path) as f:
            checklist = json.load(f)

        assert "methodology_id" in checklist
        assert "requirements" in checklist
        assert len(checklist["requirements"]) >= 20

        # Verify requirement structure
        req = checklist["requirements"][0]
        assert "requirement_id" in req
        assert "category" in req
        assert "requirement_text" in req

    def test_state_manager_atomic_update(self, test_settings):
        """State updates are atomic."""
        manager = StateManager("session-aa0000000002")

        initial = {"counter": 0, "status": "pending"}
        manager.write_json("atomic.json", initial)

        updated = manager.update_json("atomic.json", {"counter": 1})

        assert updated["counter"] == 1
        assert updated["status"] == "pending"  # Other fields preserved


@pytest.mark.smoke
class TestErrorHandling:
    """Critical error conditions are handled correctly."""

    def test_nonexistent_session_raises(self, test_settings):
        """Loading nonexistent session raises proper error."""
        from registry_review_mcp.models.errors import SessionNotFoundError

        manager = StateManager("session-aa0000000003")

        with pytest.raises(SessionNotFoundError):
            manager.read_json("nonexistent.json")

    def test_cache_nonexistent_key(self, test_settings):
        """Cache returns default for missing keys."""
        cache = Cache("smoke-missing")

        assert cache.get("nonexistent") is None
        assert cache.get("nonexistent", "default") == "default"
        assert not cache.exists("nonexistent")


@pytest.mark.smoke
class TestLazyLoadingPattern:
    """Verify heavy dependencies don't block core imports."""

    def test_core_tools_import_without_heavy_deps(self):
        """Core tools can be imported without fiona/marker.

        This validates the lazy loading pattern: heavy deps (fiona, marker)
        should only be required when calling specific functions, not at
        module import time.
        """
        # These imports should work even if fiona/marker aren't installed
        from registry_review_mcp.models import evidence
        from registry_review_mcp.tools import evidence_tools, validation_tools

        # Verify we got actual modules
        assert hasattr(validation_tools, "cross_validate")
        assert hasattr(evidence_tools, "extract_evidence_with_llm")
        assert hasattr(evidence, "EvidenceSnippet")

    def test_evidence_model_has_structured_fields(self):
        """EvidenceSnippet model supports structured_fields."""
        from registry_review_mcp.models.evidence import EvidenceSnippet

        # New field should exist with None default
        snippet = EvidenceSnippet(
            document_id="doc-001",
            document_name="test.pdf",
            text="Test evidence",
            confidence=0.95,
            page=1,
        )
        assert snippet.structured_fields is None

        # Should accept structured_fields
        snippet_with_fields = EvidenceSnippet(
            document_id="doc-001",
            document_name="test.pdf",
            text="Test evidence",
            confidence=0.95,
            page=1,
            structured_fields={"owner_name": "John Doe"},
        )
        assert snippet_with_fields.structured_fields == {"owner_name": "John Doe"}


@pytest.mark.smoke
class TestDocumentDiscoveryEdgeCases:
    """Regression tests for document discovery edge cases."""

    @pytest.mark.asyncio
    async def test_discovery_in_dot_prefixed_parent_directory(self, test_settings, tmp_path):
        """Files are found even when parent path contains dot-prefixed directories.

        Regression test for bug where files under ~/.local/share/ were skipped
        because the hidden file check examined the full absolute path instead of
        the relative path from the scan root.
        """
        # Create a path that mimics ~/.local/share structure
        dot_parent = tmp_path / ".local" / "share" / "test-uploads"
        dot_parent.mkdir(parents=True)

        # Create a test PDF file
        test_pdf = dot_parent / "test_document.pdf"
        test_pdf.write_bytes(b"%PDF-1.4 fake pdf content")

        from registry_review_mcp.tools import document_tools, session_tools

        # Create session pointing to the dot-prefixed path
        result = await session_tools.create_session(
            project_name="Dot Path Test",
            documents_path=str(dot_parent),
            methodology="soil-carbon-v1.2.2",
        )
        session_id = result["session_id"]

        # Run discovery
        discovery_result = await document_tools.discover_documents(session_id)

        # The file should be found (not skipped due to .local in path)
        assert discovery_result["documents_found"] >= 1, (
            "File in .local path should not be skipped as hidden"
        )


# Summary fixture to track smoke test health
@pytest.fixture(scope="module", autouse=True)
def smoke_test_summary(request):
    """Track smoke test execution time."""
    import time
    start = time.time()

    yield

    duration = time.time() - start
    print(f"\n\n{'='*80}")
    print(f"SMOKE TEST SUMMARY")
    print(f"{'='*80}")
    print(f"Duration: {duration:.3f}s")
    print(f"Target: < 1.0s")
    print(f"Status: {'✓ PASS' if duration < 1.0 else '✗ SLOW'}")
    print(f"{'='*80}\n")
