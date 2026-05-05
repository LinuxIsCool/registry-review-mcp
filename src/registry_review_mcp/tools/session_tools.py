"""Session management tools for creating, loading, and updating review sessions."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config.settings import settings
from ..utils.safe_delete import safe_rmtree

logger = logging.getLogger(__name__)
from ..models.schemas import (
    ProjectMetadata,
    Session,
    SessionStatistics,
    WorkflowProgress,
)
from ..utils.state import StateManager, get_session_or_raise


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"session-{uuid.uuid4().hex[:12]}"


def get_session_uploads_dir(session_id: str) -> Path:
    """Get the persistent uploads directory for a session.

    This is where uploaded documents should be stored to survive reboots.
    Located at: data/sessions/{session_id}/uploads/

    Args:
        session_id: Unique session identifier

    Returns:
        Path to the uploads directory (created if it doesn't exist)
    """
    uploads_dir = settings.get_session_path(session_id) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


async def create_session(
    project_name: str,
    methodology: str = "soil-carbon-v1.2.2",
    project_id: str | None = None,
    proponent: str | None = None,
    crediting_period: str | None = None,
    submission_date: datetime | None = None,
    documents_path: str | None = None,  # Deprecated: Use add_documents() instead
    scope: str | None = None,
) -> dict[str, Any]:
    """Create a new registry review session.

    Stage 1: Initialize session with metadata only.
    No document sources are added yet - use add_documents() for that.

    Args:
        project_name: Name of the project being reviewed
        methodology: Methodology identifier (default: soil-carbon-v1.2.2)
        project_id: Optional project ID (e.g., C06-4997)
        proponent: Optional project proponent name
        crediting_period: Optional crediting period
        submission_date: Optional submission date
        documents_path: DEPRECATED - Use add_documents() instead
        scope: Optional scope filter -- "farm" or "meta" for multi-project setups

    Returns:
        Session creation result with session_id and metadata
    """
    from ..utils.checklist import load_checklist, validate_program_guide_url

    # Validate inputs via Pydantic
    project_metadata = ProjectMetadata(
        project_name=project_name,
        project_id=project_id,
        crediting_period=crediting_period,
        submission_date=submission_date,
        methodology=methodology,
        proponent=proponent,
        scope=scope,
        documents_path=documents_path,  # Backward compatibility
    )

    # Generate session
    session_id = generate_session_id()
    now = datetime.now(timezone.utc)

    # Load checklist requirements count (filtered by scope if provided).
    # Also soft-validate the canonical methodology page URL so reviewers
    # know whether the published source has moved -- warning only, never
    # aborts session creation. The validator does a synchronous HEAD
    # request with a 2s timeout, so we hand it off to a worker thread
    # via asyncio.to_thread so the MCP event loop stays responsive while
    # the network round-trip is in flight.
    requirements_count = 0
    program_guide_url: str | None = None
    program_guide_warning: str | None = None
    try:
        checklist_data = load_checklist(methodology, scope)
        requirements_count = len(checklist_data.get("requirements", []))
        program_guide_url = checklist_data.get("program_guide_url")
        program_guide_warning = await asyncio.to_thread(validate_program_guide_url, program_guide_url)
    except FileNotFoundError:
        pass

    # Create session with empty document_sources list
    session = Session(
        session_id=session_id,
        created_at=now,
        updated_at=now,
        status="initialized",
        project_metadata=project_metadata,
        document_sources=[],  # Start with no sources
        workflow_progress=WorkflowProgress(initialize="completed"),
        statistics=SessionStatistics(requirements_total=requirements_count),
    )

    # Persist to disk
    state_manager = StateManager(session_id)
    state_manager.write_json("session.json", session.model_dump(mode="json"))

    # Initialize empty structures
    state_manager.write_json("documents.json", {"documents": []})
    state_manager.write_json("findings.json", {"findings": []})

    response: dict[str, Any] = {
        "session_id": session_id,
        "project_name": project_name,
        "created_at": now.isoformat(),
        "methodology": methodology,
        "requirements_total": requirements_count,
        "message": f"Session created successfully for project: {project_name}",
    }
    if program_guide_url:
        response["program_guide_url"] = program_guide_url
    if program_guide_warning:
        response["program_guide_warning"] = program_guide_warning
    return response


async def load_session(session_id: str) -> dict[str, Any]:
    """Load an existing session."""
    state_manager = get_session_or_raise(session_id)
    session_data = state_manager.read_json("session.json")
    return session_data


async def update_session_state(
    session_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Update session state with partial changes."""
    state_manager = get_session_or_raise(session_id)

    # Add updated timestamp
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Atomic update
    session_data = state_manager.update_json("session.json", updates)

    return session_data


async def list_sessions() -> list[dict[str, Any]]:
    """List all available sessions."""
    sessions = []

    for session_dir in settings.sessions_dir.iterdir():
        if session_dir.is_dir() and (session_dir / "session.json").exists():
            try:
                state_manager = StateManager(session_dir.name)
                session_data = state_manager.read_json("session.json")

                sessions.append(
                    {
                        "session_id": session_data.get("session_id"),
                        "project_name": session_data.get("project_metadata", {}).get("project_name"),
                        "created_at": session_data.get("created_at"),
                        "updated_at": session_data.get("updated_at"),
                        "status": session_data.get("status"),
                        "methodology": session_data.get("project_metadata", {}).get("methodology"),
                        "workflow_progress": session_data.get("workflow_progress", {}),
                        "statistics": session_data.get("statistics", {}),
                        "project_metadata": session_data.get("project_metadata", {}),
                    }
                )
            except Exception:
                # Skip corrupted sessions
                continue

    # Sort by creation date (newest first)
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return sessions


async def delete_session(session_id: str) -> dict[str, Any]:
    """Delete a session and all its data.

    Safety: Validates that the session directory is within the expected
    sessions_dir before deletion to prevent accidental data loss.

    Audit: All deletion attempts are logged for forensic analysis.
    """
    state_manager = get_session_or_raise(session_id)

    # SAFETY CHECK: Verify session_dir is within expected sessions_dir
    # This prevents accidental deletion if settings.sessions_dir was corrupted
    session_dir = state_manager.session_dir.resolve()
    expected_parent = settings.sessions_dir.resolve()

    # AUDIT: Log deletion attempt with full context
    logger.warning(f"SESSION DELETE REQUESTED: session_id={session_id}, path={session_dir}, parent={expected_parent}")

    # Ensure the session directory is a child of sessions_dir
    try:
        session_dir.relative_to(expected_parent)
    except ValueError:
        logger.error(f"SESSION DELETE BLOCKED: Security violation - {session_dir} not within {expected_parent}")
        raise ValueError(
            f"Security violation: session directory {session_dir} "
            f"is not within expected sessions directory {expected_parent}. "
            f"Deletion aborted to prevent data loss."
        )

    # Additional safety: verify it looks like a session directory
    if not (session_dir / "session.json").exists():
        logger.error(f"SESSION DELETE BLOCKED: Invalid directory - no session.json in {session_dir}")
        raise ValueError(f"Invalid session directory: {session_dir} does not contain session.json. Deletion aborted.")

    # AUDIT: Log successful deletion with timestamp
    logger.warning(f"SESSION DELETE EXECUTING: Removing {session_dir} at {datetime.now(timezone.utc).isoformat()}")

    # Remove entire session directory using safe_rmtree
    # force=True because we've done our own validation above
    safe_rmtree(session_dir, force=True)

    logger.info(f"SESSION DELETE COMPLETE: {session_id} removed successfully")

    return {
        "session_id": session_id,
        "status": "deleted",
        "message": f"Session {session_id} has been deleted",
    }


async def list_example_projects() -> dict[str, Any]:
    """List example projects available in the examples directory."""

    # Get examples directory relative to settings
    examples_dir = settings.data_dir.parent / "examples"

    if not examples_dir.exists():
        return {
            "projects_found": 0,
            "projects": [],
            "message": "No examples directory found",
        }

    projects = []
    for item in sorted(examples_dir.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            # Count files in the directory
            file_count = sum(1 for f in item.rglob("*") if f.is_file())
            projects.append(
                {
                    "name": item.name,
                    "path": str(item.absolute()),
                    "file_count": file_count,
                }
            )

    return {
        "projects_found": len(projects),
        "projects": projects,
        "message": f"Found {len(projects)} example project(s)" if projects else "No example projects found",
    }
