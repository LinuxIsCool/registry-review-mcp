"""Tests for multi-project scope support and centralized checklist loading.

Adopted Becca's V2 template (2026-04-28). Counts and IDs follow the
``C06-REGISTRATION-ML-NNN`` pattern. The aggregate project axis introduced
in V2 expands per-farm requirements from 4 to 16 and reduces the meta
total from 19 to 14, while the full set grew from 23 to 30.
"""

import pytest

from registry_review_mcp.utils.checklist import load_checklist

METHODOLOGY = "soil-carbon-v1.2.2"

# V2 farm-scope IDs cover every requirement that explicitly states
# "For Aggregate Projects, each site must demonstrate compliance" or
# otherwise applies at the per-Site level.
FARM_REQ_IDS = {
    "C06-REGISTRATION-ML-003",  # Ecosystem Type
    "C06-REGISTRATION-ML-004",  # Land Tenure
    "C06-REGISTRATION-ML-005",  # Project Area — 10y conversion
    "C06-REGISTRATION-ML-006",  # Project Area — GIS boundaries
    "C06-REGISTRATION-ML-009",  # Aggregate — homogeneity
    "C06-REGISTRATION-ML-010",  # Aggregate — Site identifiability
    "C06-REGISTRATION-ML-011",  # Aggregate — per-Site monitoring
    "C06-REGISTRATION-ML-014",  # Project Start Date (per Site for aggregates)
    "C06-REGISTRATION-ML-016",  # Additionality — legality
    "C06-REGISTRATION-ML-017",  # Additionality — BAU
    "C06-REGISTRATION-ML-018",  # Additionality — drawdown expectation
    "C06-REGISTRATION-ML-019",  # Leakage — yield rolling average
    "C06-REGISTRATION-ML-020",  # Leakage — land-use continuity
    "C06-REGISTRATION-ML-022",  # Regulatory Compliance
    "C06-REGISTRATION-ML-023",  # Registration on Other Registries
    "C06-REGISTRATION-ML-029",  # Project Activity records
}

EXPECTED_TOTAL = 30
EXPECTED_FARM = 16
EXPECTED_META = 14


class TestLoadChecklist:
    """Tests for the centralized checklist loader."""

    def test_load_checklist_no_scope(self):
        """Loading with no scope returns all V2 requirements."""
        data = load_checklist(METHODOLOGY)
        requirements = data["requirements"]
        assert len(requirements) == EXPECTED_TOTAL

    def test_load_checklist_farm_scope(self):
        """Loading with scope='farm' returns the V2 per-Site requirements."""
        data = load_checklist(METHODOLOGY, scope="farm")
        requirements = data["requirements"]
        assert len(requirements) == EXPECTED_FARM
        req_ids = {r["requirement_id"] for r in requirements}
        assert req_ids == FARM_REQ_IDS

    def test_load_checklist_meta_scope(self):
        """Loading with scope='meta' returns the V2 project-level requirements."""
        data = load_checklist(METHODOLOGY, scope="meta")
        requirements = data["requirements"]
        assert len(requirements) == EXPECTED_META
        req_ids = {r["requirement_id"] for r in requirements}
        assert req_ids & FARM_REQ_IDS == set()

    def test_load_checklist_preserves_metadata(self):
        """Scope filtering preserves top-level checklist metadata."""
        data = load_checklist(METHODOLOGY, scope="farm")
        assert data["methodology_id"] == METHODOLOGY
        assert data["version"] == "1.2.2"
        assert "protocol" in data

    def test_load_checklist_farm_plus_meta_equals_all(self):
        """Farm + meta requirements together equal the full set."""
        farm = load_checklist(METHODOLOGY, scope="farm")
        meta = load_checklist(METHODOLOGY, scope="meta")
        full = load_checklist(METHODOLOGY)
        farm_ids = {r["requirement_id"] for r in farm["requirements"]}
        meta_ids = {r["requirement_id"] for r in meta["requirements"]}
        full_ids = {r["requirement_id"] for r in full["requirements"]}
        assert farm_ids | meta_ids == full_ids
        assert farm_ids & meta_ids == set()

    def test_load_checklist_missing_methodology(self):
        """Loading a nonexistent methodology raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_checklist("nonexistent-methodology-v99")

    def test_every_requirement_has_scope(self):
        """Every requirement in the checklist has an explicit scope field."""
        data = load_checklist(METHODOLOGY)
        for req in data["requirements"]:
            assert "scope" in req, f"{req['requirement_id']} missing scope field"
            assert req["scope"] in ("farm", "meta"), (
                f"{req['requirement_id']} has invalid scope: {req['scope']}"
            )


class TestSessionCreationWithScope:
    """Tests for session creation with scope parameter."""

    @pytest.mark.asyncio
    async def test_session_creation_with_farm_scope(self):
        """Creating a session with scope='farm' sets requirements_total to V2 farm count."""
        from registry_review_mcp.tools.session_tools import create_session

        result = await create_session(
            project_name="Test Farm Session",
            scope="farm",
        )
        assert result["requirements_total"] == EXPECTED_FARM

    @pytest.mark.asyncio
    async def test_session_creation_with_meta_scope(self):
        """Creating a session with scope='meta' sets requirements_total to V2 meta count."""
        from registry_review_mcp.tools.session_tools import create_session

        result = await create_session(
            project_name="Test Meta Session",
            scope="meta",
        )
        assert result["requirements_total"] == EXPECTED_META

    @pytest.mark.asyncio
    async def test_session_creation_no_scope(self):
        """Creating a session without scope sets requirements_total to V2 total."""
        from registry_review_mcp.tools.session_tools import create_session

        result = await create_session(
            project_name="Test Full Session",
        )
        assert result["requirements_total"] == EXPECTED_TOTAL

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        """Two sessions with different scopes have independent state."""
        from registry_review_mcp.tools.session_tools import create_session

        farm = await create_session(project_name="Farm A", scope="farm")
        meta = await create_session(project_name="Meta Project", scope="meta")

        assert farm["session_id"] != meta["session_id"]
        assert farm["requirements_total"] == EXPECTED_FARM
        assert meta["requirements_total"] == EXPECTED_META
