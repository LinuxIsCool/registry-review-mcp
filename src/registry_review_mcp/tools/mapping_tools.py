"""Requirement mapping tools for mapping documents to checklist requirements.

Stage 3 of the registry review workflow.
"""

from datetime import datetime, timezone
from typing import Any

from ..models.schemas import (
    MappingCollection,
    RequirementMapping,
)
from ..utils.checklist import load_checklist
from ..utils.state import get_session_or_raise


async def map_all_requirements(session_id: str) -> dict[str, Any]:
    """Map all requirements to documents using semantic matching.

    This function:
    1. Loads the checklist for the session methodology
    2. Loads discovered documents from Stage 2
    3. For each requirement, suggests document matches
    4. Generates confidence scores for suggestions
    5. Stores mappings in mappings.json

    Args:
        session_id: Unique session identifier

    Returns:
        Summary of mapping results with statistics
    """
    state_manager = get_session_or_raise(session_id)

    # Load session to get methodology
    session_data = state_manager.read_json("session.json")
    methodology = session_data.get("project_metadata", {}).get("methodology", "soil-carbon-v1.2.2")

    # Load checklist (filtered by scope if set in session)
    scope = session_data.get("project_metadata", {}).get("scope")
    checklist_data = load_checklist(methodology, scope)
    requirements = checklist_data.get("requirements", [])

    # Load discovered documents
    documents_data = state_manager.read_json("documents.json")
    documents = documents_data.get("documents", documents_data)

    if not documents:
        raise ValueError("No documents discovered. Run document discovery first (Stage 2).")

    # Create document lookup by ID and classification
    doc_by_id = {doc["document_id"]: doc for doc in documents}
    docs_by_type = {}
    for doc in documents:
        classification = doc.get("classification", "unknown")
        if classification not in docs_by_type:
            docs_by_type[classification] = []
        docs_by_type[classification].append(doc)

    # Map each requirement to documents
    mappings = []
    mapped_count = 0
    unmapped_count = 0

    for req in requirements:
        requirement_id = req["requirement_id"]
        category = req.get("category", "")
        accepted_evidence = req.get("accepted_evidence", "").lower()

        # Determine expected document types based on requirement
        expected_types = _infer_document_types(category, accepted_evidence)

        # Find matching documents
        matched_docs = []
        confidence = 0.0

        for doc_type in expected_types:
            if doc_type in docs_by_type:
                for doc in docs_by_type[doc_type]:
                    matched_docs.append(doc["document_id"])

        # Calculate confidence based on match quality
        if matched_docs:
            # High confidence if we found expected type
            confidence = 0.85 if len(matched_docs) == 1 else 0.75
            mapping_status = "suggested"
            mapped_count += 1
        else:
            # Try fallback: project plan often contains most information
            if "project_plan" in docs_by_type:
                project_plans = docs_by_type.get("project_plan", [])
                if project_plans:
                    matched_docs = [project_plans[0]["document_id"]]
                    confidence = 0.50  # Lower confidence for fallback
                    mapping_status = "suggested"
                    mapped_count += 1
                else:
                    mapping_status = "unmapped"
                    confidence = 0.0
                    unmapped_count += 1
            else:
                mapping_status = "unmapped"
                confidence = 0.0
                unmapped_count += 1

        # Create mapping
        mapping = RequirementMapping(
            requirement_id=requirement_id,
            mapped_documents=matched_docs,
            mapping_status=mapping_status,
            confidence=confidence,
            suggested_by="agent",
            confirmed_by=None,
            confirmed_at=None,
        )

        mappings.append(mapping)

    # Create collection
    now = datetime.now(timezone.utc)
    collection = MappingCollection(
        session_id=session_id,
        mappings=mappings,
        total_requirements=len(requirements),
        mapped_count=mapped_count,
        unmapped_count=unmapped_count,
        confirmed_count=0,  # None confirmed yet
        created_at=now,
        updated_at=now,
    )

    # Save to state
    state_manager.write_json("mappings.json", collection.model_dump(mode="json"))

    # Update workflow progress
    state_manager.update_json(
        "session.json",
        {
            "workflow_progress.requirement_mapping": "completed",
            "statistics.requirements_covered": mapped_count,
        },
    )

    # Build requirements matrix for display
    requirements_matrix = []
    needs_attention = []
    high_confidence_count = 0
    medium_confidence_count = 0
    low_confidence_count = 0

    for mapping, req in zip(mappings, requirements):
        confidence = mapping.confidence
        status = mapping.mapping_status

        # Categorize by confidence level
        if status == "unmapped":
            confidence_level = "unmapped"
        elif confidence > 0.75:
            confidence_level = "high"
            high_confidence_count += 1
        elif confidence >= 0.5:
            confidence_level = "medium"
            medium_confidence_count += 1
        else:
            confidence_level = "low"
            low_confidence_count += 1

        # Status icon for visual display
        if status == "confirmed":
            status_icon = "✓"
        elif confidence > 0.75:
            status_icon = "●"
        elif confidence >= 0.5:
            status_icon = "○"
        elif confidence > 0:
            status_icon = "◌"
        else:
            status_icon = "✗"

        row = {
            "requirement_id": mapping.requirement_id,
            "category": req.get("category", ""),
            "requirement_text": req.get("requirement_text", "")[:80] + "..."
            if len(req.get("requirement_text", "")) > 80
            else req.get("requirement_text", ""),
            "status": status,
            "confidence": confidence,
            "confidence_level": confidence_level,
            "status_icon": status_icon,
            "mapped_documents": mapping.mapped_documents,
        }
        requirements_matrix.append(row)

        # Track items needing attention
        if status == "unmapped" or confidence < 0.75:
            needs_attention.append(
                {
                    "requirement_id": mapping.requirement_id,
                    "category": req.get("category", ""),
                    "issue": "Unmapped" if status == "unmapped" else f"Low confidence ({confidence:.0%})",
                    "recommendation": "Requires manual document assignment"
                    if status == "unmapped"
                    else "Review suggested mapping before confirming",
                }
            )

    # Build document summary
    doc_summary = []
    for doc in documents:
        doc_summary.append(
            {
                "id": doc["document_id"],
                "filename": doc.get("filename", doc["document_id"]),
                "type": doc.get("classification", "unknown"),
            }
        )

    return {
        "session_id": session_id,
        "summary": {
            "total_requirements": len(requirements),
            "mapped_count": mapped_count,
            "unmapped_count": unmapped_count,
            "confirmed_count": 0,
            "coverage_rate": mapped_count / len(requirements) if len(requirements) > 0 else 0.0,
            "confidence_breakdown": {
                "high": high_confidence_count,
                "medium": medium_confidence_count,
                "low": low_confidence_count,
            },
        },
        "documents": doc_summary,
        "requirements_matrix": requirements_matrix,
        "insights": {
            "items_needing_attention": len(needs_attention),
            "attention_details": needs_attention if needs_attention else None,
            "recommendation": (
                "All mappings have high confidence. Review and confirm to proceed."
                if not needs_attention
                else f"{len(needs_attention)} item(s) need review. Check attention_details for specifics."
            ),
        },
        "legend": {
            "✓": "Confirmed by human",
            "●": "Suggested (high confidence >75%)",
            "○": "Suggested (medium confidence 50-75%)",
            "◌": "Suggested (low confidence <50%)",
            "✗": "Unmapped (needs attention)",
        },
        "next_steps": [
            "1. Review the requirements_matrix for all mappings",
            "2. Check items_needing_attention for potential issues",
            "3. Use confirm_all_mappings to accept all suggestions, or confirm_mapping for individual items",
            "4. Once satisfied, proceed to evidence extraction",
        ],
        "message": f"Mapped {mapped_count}/{len(requirements)} requirements to documents",
    }


def _infer_document_types(category: str, accepted_evidence: str) -> list[str]:
    """Infer expected document types from requirement category and evidence description.

    Classification labels use underscores (e.g., "project_plan", "land_tenure")
    to match the output of classify_document_by_filename() in document_tools.py.

    Args:
        category: Requirement category (e.g., "Land Tenure", "Baseline")
        accepted_evidence: Description of accepted evidence

    Returns:
        List of document classification types that might contain this evidence
    """
    category_lower = category.lower()
    evidence_lower = accepted_evidence.lower()

    # Map requirement categories to classifier output labels.
    # Labels must match classify_document_by_filename() in document_tools.py.
    #
    # The keys cover both the legacy v1 categories ("GHG Accounting", "Risk
    # Management") and Becca's v2 template categories
    # ("Additionality", "Leakage", "Permanence Period", "Aggregate Project",
    # "Project Activity", "Project Plan"). v2 split "GHG Accounting" into
    # Additionality + Leakage + Permanence Period, so we route each piece to
    # the same evidence types the v1 lookup did.
    type_mapping = {
        "land tenure": ["land_tenure", "spreadsheet_data", "project_plan"],
        "land eligibility": ["land_tenure", "spreadsheet_data", "project_plan"],
        "baseline": ["baseline_report", "project_plan"],
        "monitoring": ["monitoring_report", "spreadsheet_data"],
        "sampling": ["monitoring_report", "spreadsheet_data"],
        "gis": ["gis_shapefile", "land_cover_map", "project_plan"],
        "emissions": ["ghg_emissions", "monitoring_report", "spreadsheet_data"],
        "project details": ["project_plan", "spreadsheet_data"],
        "project area": ["project_plan", "gis_shapefile", "land_cover_map"],
        "project boundary": ["project_plan", "gis_shapefile"],
        "project ownership": ["project_plan", "land_tenure", "spreadsheet_data"],
        "project start date": ["project_plan"],
        "project plan": ["project_plan"],
        "project activity": ["project_plan", "monitoring_report", "spreadsheet_data"],
        "ecosystem type": ["project_plan", "baseline_report", "spreadsheet_data", "land_cover_map"],
        "crediting period": ["project_plan"],
        "aggregate project": ["project_plan", "spreadsheet_data", "land_tenure"],
        "ghg accounting": ["project_plan", "ghg_emissions", "monitoring_report"],
        "additionality": ["project_plan", "ghg_emissions", "baseline_report", "monitoring_report"],
        "leakage": ["project_plan", "monitoring_report", "spreadsheet_data", "ghg_emissions"],
        "permanence period": ["project_plan", "monitoring_report"],
        "regulatory compliance": ["project_plan"],
        "registration on other registries": ["project_plan"],
        "project plan deviations": ["project_plan"],
        "monitoring plan": ["project_plan", "monitoring_report"],
        "risk management": ["project_plan"],
        "safeguards": ["project_plan"],
    }

    # Check category matches
    for key, types in type_mapping.items():
        if key in category_lower:
            return types

    # Check evidence description keywords
    if "deed" in evidence_lower or "title" in evidence_lower or "ownership" in evidence_lower:
        return ["land_tenure", "spreadsheet_data", "project_plan"]
    elif "map" in evidence_lower or "shapefile" in evidence_lower or "gis" in evidence_lower:
        return ["gis_shapefile", "land_cover_map", "project_plan"]
    elif "baseline" in evidence_lower:
        return ["baseline_report", "project_plan"]
    elif "monitoring" in evidence_lower or "sampling" in evidence_lower:
        return ["monitoring_report"]
    elif "emission" in evidence_lower or "ghg" in evidence_lower:
        return ["ghg_emissions", "monitoring_report"]

    # Default: project plan is the most comprehensive document
    return ["project_plan"]


async def confirm_mapping(
    session_id: str,
    requirement_id: str,
    document_ids: list[str],
    confirmed_by: str = "user",
) -> dict[str, Any]:
    """Confirm or manually set document mappings for a requirement.

    Args:
        session_id: Unique session identifier
        requirement_id: Requirement ID to update
        document_ids: List of document IDs to map to this requirement
        confirmed_by: Username or identifier of person confirming

    Returns:
        Updated mapping information
    """
    state_manager = get_session_or_raise(session_id)

    # Load current mappings
    mappings_data = state_manager.read_json("mappings.json")

    # Find and update the mapping
    updated = False
    for mapping in mappings_data["mappings"]:
        if mapping["requirement_id"] == requirement_id:
            mapping["mapped_documents"] = document_ids
            mapping["mapping_status"] = "confirmed"
            mapping["confirmed_by"] = confirmed_by
            mapping["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            mapping["suggested_by"] = "manual"
            updated = True
            break

    if not updated:
        raise ValueError(f"Requirement not found: {requirement_id}")

    # Recalculate statistics
    total = len(mappings_data["mappings"])
    confirmed = sum(1 for m in mappings_data["mappings"] if m["mapping_status"] == "confirmed")
    mapped = sum(1 for m in mappings_data["mappings"] if m["mapped_documents"])
    unmapped = total - mapped

    mappings_data["confirmed_count"] = confirmed
    mappings_data["mapped_count"] = mapped
    mappings_data["unmapped_count"] = unmapped
    mappings_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Save updated mappings
    state_manager.write_json("mappings.json", mappings_data)

    return {
        "requirement_id": requirement_id,
        "mapped_documents": document_ids,
        "mapping_status": "confirmed",
        "confirmed_by": confirmed_by,
        "message": f"Confirmed mapping for {requirement_id}",
    }


async def remove_mapping(
    session_id: str,
    requirement_id: str,
    document_id: str,
) -> dict[str, Any]:
    """Remove a document from a requirement mapping.

    Args:
        session_id: Unique session identifier
        requirement_id: Requirement ID to update
        document_id: Document ID to remove from mapping

    Returns:
        Updated mapping information
    """
    state_manager = get_session_or_raise(session_id)

    # Load current mappings
    mappings_data = state_manager.read_json("mappings.json")

    # Find and update the mapping
    updated = False
    for mapping in mappings_data["mappings"]:
        if mapping["requirement_id"] == requirement_id:
            if document_id in mapping["mapped_documents"]:
                mapping["mapped_documents"].remove(document_id)

                # Update status based on remaining documents
                if not mapping["mapped_documents"]:
                    mapping["mapping_status"] = "unmapped"
                    mapping["confidence"] = 0.0

                mapping["updated_at"] = datetime.now(timezone.utc).isoformat()
                updated = True
                break

    if not updated:
        raise ValueError(f"Requirement or document not found: {requirement_id}, {document_id}")

    # Recalculate statistics
    total = len(mappings_data["mappings"])
    mapped = sum(1 for m in mappings_data["mappings"] if m["mapped_documents"])
    unmapped = total - mapped

    mappings_data["mapped_count"] = mapped
    mappings_data["unmapped_count"] = unmapped
    mappings_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Save updated mappings
    state_manager.write_json("mappings.json", mappings_data)

    return {
        "requirement_id": requirement_id,
        "document_id": document_id,
        "remaining_documents": mappings_data["mappings"],
        "message": f"Removed {document_id} from {requirement_id} mapping",
    }


async def get_mapping_status(session_id: str) -> dict[str, Any]:
    """Get current mapping status and statistics.

    Args:
        session_id: Unique session identifier

    Returns:
        Complete mapping status with statistics and details
    """
    state_manager = get_session_or_raise(session_id)

    # Load mappings
    mappings_data = state_manager.read_json("mappings.json")

    # Calculate detailed statistics
    total = mappings_data.get("total_requirements", 0)
    mapped = mappings_data.get("mapped_count", 0)
    unmapped = mappings_data.get("unmapped_count", 0)
    confirmed = mappings_data.get("confirmed_count", 0)

    # Breakdown by confidence level
    high_confidence = sum(1 for m in mappings_data["mappings"] if m.get("confidence", 0) > 0.75)
    medium_confidence = sum(1 for m in mappings_data["mappings"] if 0.5 <= m.get("confidence", 0) <= 0.75)
    low_confidence = sum(1 for m in mappings_data["mappings"] if 0 < m.get("confidence", 0) < 0.5)

    return {
        "session_id": session_id,
        "total_requirements": total,
        "mapped_count": mapped,
        "unmapped_count": unmapped,
        "confirmed_count": confirmed,
        "coverage_rate": mapped / total if total > 0 else 0.0,
        "confidence_breakdown": {
            "high": high_confidence,
            "medium": medium_confidence,
            "low": low_confidence,
        },
        "created_at": mappings_data.get("created_at"),
        "updated_at": mappings_data.get("updated_at"),
    }


async def get_mapping_matrix(session_id: str) -> dict[str, Any]:
    """Get a visual matrix view of document-to-requirement mappings.

    Returns a structured matrix showing which documents map to which requirements,
    with confidence indicators and status. Designed for human review before
    evidence extraction.

    Args:
        session_id: Unique session identifier

    Returns:
        Matrix structure with documents, requirements, and mapping indicators
    """
    state_manager = get_session_or_raise(session_id)

    # Load mappings
    mappings_data = state_manager.read_json("mappings.json")

    # Load documents
    documents_data = state_manager.read_json("documents.json")
    documents = documents_data.get("documents", documents_data)

    # Load checklist for requirement details
    session_data = state_manager.read_json("session.json")
    methodology = session_data.get("project_metadata", {}).get("methodology", "soil-carbon-v1.2.2")
    scope = session_data.get("project_metadata", {}).get("scope")
    checklist_data = load_checklist(methodology, scope)
    requirements = {r["requirement_id"]: r for r in checklist_data.get("requirements", [])}

    # Build document info
    doc_info = []
    for doc in documents:
        doc_info.append(
            {
                "id": doc["document_id"],
                "name": doc.get("filename", doc["document_id"])[:25],
                "type": doc.get("classification", "unknown"),
            }
        )

    # Build matrix rows
    matrix_rows = []
    for mapping in mappings_data["mappings"]:
        req_id = mapping["requirement_id"]
        req_data = requirements.get(req_id, {})

        # Determine status icon
        status = mapping.get("mapping_status", "unmapped")
        confidence = mapping.get("confidence", 0)

        if status == "confirmed":
            status_icon = "✓"
        elif status == "suggested" and confidence > 0.75:
            status_icon = "●"  # High confidence suggestion
        elif status == "suggested" and confidence >= 0.5:
            status_icon = "○"  # Medium confidence
        elif status == "suggested":
            status_icon = "◌"  # Low confidence
        else:
            status_icon = "✗"  # Unmapped

        # Build document mapping indicators
        doc_mappings = {}
        mapped_docs = mapping.get("mapped_documents", [])
        for doc in doc_info:
            if doc["id"] in mapped_docs:
                doc_mappings[doc["id"]] = status_icon
            else:
                doc_mappings[doc["id"]] = "·"

        matrix_rows.append(
            {
                "requirement_id": req_id,
                "category": req_data.get("category", ""),
                "brief": req_data.get("requirement_text", "")[:50] + "..."
                if len(req_data.get("requirement_text", "")) > 50
                else req_data.get("requirement_text", ""),
                "status": status,
                "confidence": confidence,
                "status_icon": status_icon,
                "doc_mappings": doc_mappings,
                "mapped_documents": mapped_docs,
            }
        )

    # Calculate summary
    total = len(matrix_rows)
    confirmed = sum(1 for r in matrix_rows if r["status"] == "confirmed")
    suggested = sum(1 for r in matrix_rows if r["status"] == "suggested")
    unmapped = sum(1 for r in matrix_rows if r["status"] == "unmapped")

    return {
        "session_id": session_id,
        "documents": doc_info,
        "requirements": matrix_rows,
        "summary": {
            "total": total,
            "confirmed": confirmed,
            "suggested": suggested,
            "unmapped": unmapped,
            "ready_for_extraction": confirmed == total or (confirmed + suggested == total and unmapped == 0),
        },
        "legend": {
            "✓": "Confirmed by human",
            "●": "Suggested (high confidence >75%)",
            "○": "Suggested (medium confidence 50-75%)",
            "◌": "Suggested (low confidence <50%)",
            "✗": "Unmapped (needs attention)",
            "·": "Not mapped to this document",
        },
    }


async def confirm_all_mappings(
    session_id: str,
    confirmed_by: str = "user",
) -> dict[str, Any]:
    """Confirm all suggested mappings in bulk.

    This is a convenience function for when the user has reviewed the matrix
    and wants to accept all agent suggestions.

    Args:
        session_id: Unique session identifier
        confirmed_by: Username or identifier of person confirming

    Returns:
        Summary of confirmations made
    """
    state_manager = get_session_or_raise(session_id)

    # Load current mappings
    mappings_data = state_manager.read_json("mappings.json")

    confirmed_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for mapping in mappings_data["mappings"]:
        if mapping["mapping_status"] == "suggested" and mapping.get("mapped_documents"):
            mapping["mapping_status"] = "confirmed"
            mapping["confirmed_by"] = confirmed_by
            mapping["confirmed_at"] = now
            confirmed_count += 1

    # Update statistics
    total_confirmed = sum(1 for m in mappings_data["mappings"] if m["mapping_status"] == "confirmed")
    mappings_data["confirmed_count"] = total_confirmed
    mappings_data["updated_at"] = now

    # Save updated mappings
    state_manager.write_json("mappings.json", mappings_data)

    # Update session to indicate mappings are confirmed
    state_manager.update_json(
        "session.json",
        {
            "workflow_progress.mappings_confirmed": True,
            "statistics.mappings_confirmed": total_confirmed,
        },
    )

    return {
        "session_id": session_id,
        "newly_confirmed": confirmed_count,
        "total_confirmed": total_confirmed,
        "confirmed_by": confirmed_by,
        "message": f"Confirmed {confirmed_count} suggested mappings. Total confirmed: {total_confirmed}",
    }


def format_matrix_text(matrix_data: dict[str, Any], max_doc_cols: int = 6) -> str:
    """Format matrix data as readable text for display.

    Args:
        matrix_data: Output from get_mapping_matrix
        max_doc_cols: Maximum document columns to show (for wide matrices)

    Returns:
        Formatted text representation of the matrix
    """
    docs = matrix_data["documents"][:max_doc_cols]
    rows = matrix_data["requirements"]
    summary = matrix_data["summary"]

    # Build header
    lines = []
    lines.append("## Document × Requirement Mapping Matrix\n")

    # Document header row
    doc_headers = [d["name"][:12] for d in docs]
    header = "| Req ID   | Category        | " + " | ".join(f"{h:^12}" for h in doc_headers) + " | Status |"
    separator = "|" + "-" * 10 + "|" + "-" * 17 + "|" + "|".join("-" * 14 for _ in docs) + "|" + "-" * 8 + "|"

    lines.append(header)
    lines.append(separator)

    # Requirement rows
    for row in rows:
        req_id = row["requirement_id"]
        category = row["category"][:15]
        status = row["status_icon"]

        # Document indicators
        doc_indicators = []
        for doc in docs:
            indicator = row["doc_mappings"].get(doc["id"], "·")
            doc_indicators.append(f"{indicator:^12}")

        line = f"| {req_id:<8} | {category:<15} | " + " | ".join(doc_indicators) + f" | {status:^6} |"
        lines.append(line)

    # Summary
    lines.append("")
    lines.append("### Summary")
    lines.append(f"- **Confirmed:** {summary['confirmed']}/{summary['total']}")
    lines.append(f"- **Suggested:** {summary['suggested']}/{summary['total']}")
    lines.append(f"- **Unmapped:** {summary['unmapped']}/{summary['total']}")
    lines.append("")

    if summary["ready_for_extraction"]:
        lines.append("✅ **Ready for evidence extraction**")
    else:
        lines.append("⚠️ **Review required before evidence extraction**")

    # Legend
    lines.append("")
    lines.append("### Legend")
    for symbol, meaning in matrix_data["legend"].items():
        lines.append(f"- {symbol} = {meaning}")

    return "\n".join(lines)
