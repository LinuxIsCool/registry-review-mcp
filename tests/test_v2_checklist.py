"""Tests for the v2 Becca-aligned soil-carbon-v1.2.2 checklist.

Becca emailed the official V2 Project Registration Registry Agent Review
template at 06:38 PDT on 2026-04-28. This suite verifies that our bundled
checklist faithfully reflects that template:

- 30 requirements (28 numbered in V2 + 2 orphans we explicitly assigned).
- All requirement IDs follow the locked pattern ``C06-REGISTRATION-ML-NNN``.
- The Pydantic ``Requirement.requirement_id`` regex now accepts both the
  legacy ``REQ-NNN`` shape (for backward-compat with existing fixtures)
  and the V2 ``CNN-AAA-LL-NNN`` shape.
- The bundled checklist round-trips through the ``Checklist`` model with
  no validation errors and exposes every category Becca's template names.
- ``RequirementMapping.requirement_id`` accepts the V2 pattern so review
  sessions can carry V2-shaped IDs through to mappings, evidence
  extraction, and findings.

All tests are pure (no I/O outside the bundled checklist file, no network).
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from registry_review_mcp.models.schemas import Checklist, Requirement, RequirementMapping
from registry_review_mcp.utils.checklist import load_checklist


# ---------------------------------------------------------------------------
# Constants describing what Becca's V2 template said
# ---------------------------------------------------------------------------

EXPECTED_TOTAL_REQUIREMENTS = 30
EXPECTED_TEMPLATE_VERSION = "v2-2026-04-28"
EXPECTED_CATEGORIES = {
    "General",
    "Project Plan",
    "Ecosystem Type",
    "Land Tenure",
    "Project Area",
    "Aggregate Project",
    "Project Boundary",
    "Project Ownership",
    "Project Start Date",
    "Crediting Period",
    "Additionality",
    "Leakage",
    "Permanence Period",
    "Regulatory Compliance",
    "Registration on Other Registries",
    "Safeguards",
    "Project Plan Deviations",
    "Project Activity",
    "Monitoring Plan",
}
V2_ID_PATTERN = re.compile(r"^C\d{2}-[A-Z]+-[A-Z]{2}-\d{3}$")
LEGACY_OR_V2_PATTERN = re.compile(r"^(REQ-\d{3}|C\d{2}-[A-Z]+-[A-Z]{2}-\d{3})$")


# ---------------------------------------------------------------------------
# Bundled checklist shape
# ---------------------------------------------------------------------------


def test_bundled_checklist_has_thirty_requirements() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    assert len(data["requirements"]) == EXPECTED_TOTAL_REQUIREMENTS


def test_bundled_checklist_template_version_is_v2() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    assert data.get("checklist_template_version") == EXPECTED_TEMPLATE_VERSION


def test_bundled_checklist_template_source_is_attributed_to_becca() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    source = data.get("checklist_template_source", "")
    assert "Becca" in source
    assert "2026-04-28" in source


def test_bundled_checklist_all_ids_match_v2_pattern() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    for req in data["requirements"]:
        assert V2_ID_PATTERN.match(req["requirement_id"]), (
            f"requirement_id {req['requirement_id']} does not match V2 pattern"
        )


def test_bundled_checklist_all_ids_unique() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    ids = [req["requirement_id"] for req in data["requirements"]]
    assert len(ids) == len(set(ids))


def test_bundled_checklist_ids_run_001_through_030() -> None:
    """V2 numbering must be contiguous from ML-001 through ML-030."""
    data = load_checklist("soil-carbon-v1.2.2")
    ids = sorted(req["requirement_id"] for req in data["requirements"])
    expected = sorted(f"C06-REGISTRATION-ML-{n:03d}" for n in range(1, 31))
    assert ids == expected


def test_bundled_checklist_categories_match_becca_template() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    categories = {req["category"] for req in data["requirements"]}
    assert categories == EXPECTED_CATEGORIES


def test_bundled_checklist_round_trips_through_pydantic() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    checklist = Checklist.model_validate(data)
    assert len(checklist.requirements) == EXPECTED_TOTAL_REQUIREMENTS
    assert all(
        V2_ID_PATTERN.match(r.requirement_id) for r in checklist.requirements
    )


def test_bundled_checklist_includes_aggregate_project_block() -> None:
    """V2 introduced five new Aggregate Project requirements (ML-007..ML-011)."""
    data = load_checklist("soil-carbon-v1.2.2")
    aggregate_ids = {
        req["requirement_id"]
        for req in data["requirements"]
        if req["category"] == "Aggregate Project"
    }
    assert aggregate_ids == {
        "C06-REGISTRATION-ML-007",
        "C06-REGISTRATION-ML-008",
        "C06-REGISTRATION-ML-009",
        "C06-REGISTRATION-ML-010",
        "C06-REGISTRATION-ML-011",
    }


def test_bundled_checklist_scope_filtering_still_works() -> None:
    """Scope filter must produce a non-empty subset for both farm and meta."""
    farm = load_checklist("soil-carbon-v1.2.2", scope="farm")
    meta = load_checklist("soil-carbon-v1.2.2", scope="meta")
    assert len(farm["requirements"]) > 0
    assert len(meta["requirements"]) > 0
    assert (
        len(farm["requirements"]) + len(meta["requirements"])
        == EXPECTED_TOTAL_REQUIREMENTS
    )


# ---------------------------------------------------------------------------
# requirement_id regex — both legacy and V2 patterns are accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rid",
    [
        "REQ-001",
        "REQ-099",
        "C06-REGISTRATION-ML-001",
        "C06-REGISTRATION-ML-030",
        "C07-MONITORING-ML-005",
        "C12-VERIFICATION-XX-099",
    ],
)
def test_requirement_id_pattern_accepts_valid_shapes(rid: str) -> None:
    assert LEGACY_OR_V2_PATTERN.match(rid)
    Requirement(
        requirement_id=rid,
        category="General",
        requirement_text="placeholder",
        source="placeholder",
        accepted_evidence="placeholder",
        validation_type="document_presence",
    )


@pytest.mark.parametrize(
    "rid",
    [
        "CO6-REGISTRATION-ML-001",   # letter O instead of digit 0
        "06-REGISTRATION-ML-014",    # missing leading C
        "REQ-1",                     # not three digits
        "c06-registration-ml-001",   # lowercase
        "C06-Registration-ML-001",   # mixed case
        "C06-REGISTRATION-MLX-001",  # too many letters in third segment
        "C06-REGISTRATION-ML-1",     # not three digits
        "",                          # empty
    ],
)
def test_requirement_id_pattern_rejects_malformed_shapes(rid: str) -> None:
    with pytest.raises(ValidationError):
        Requirement(
            requirement_id=rid,
            category="General",
            requirement_text="placeholder",
            source="placeholder",
            accepted_evidence="placeholder",
            validation_type="document_presence",
        )


def test_requirement_mapping_accepts_v2_pattern() -> None:
    """RequirementMapping.requirement_id must accept the V2 shape end-to-end."""
    mapping = RequirementMapping(
        requirement_id="C06-REGISTRATION-ML-014",
        mapped_documents=["doc-1"],
    )
    assert mapping.requirement_id == "C06-REGISTRATION-ML-014"


def test_requirement_mapping_still_accepts_legacy_pattern() -> None:
    """Legacy fixtures must keep working through the regex change."""
    mapping = RequirementMapping(
        requirement_id="REQ-007",
        mapped_documents=["doc-1"],
    )
    assert mapping.requirement_id == "REQ-007"
