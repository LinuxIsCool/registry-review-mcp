"""Centralized checklist loading with optional scope filtering.

Replaces the repeated json.load() pattern scattered across evidence_tools,
analyze_llm, mapping_tools, session_tools, and C_requirement_mapping.
"""

import json
import urllib.error
import urllib.request

from ..config.settings import settings


def load_checklist(methodology: str, scope: str | None = None) -> dict:
    """Load a methodology checklist, optionally filtering requirements by scope.

    Args:
        methodology: Methodology identifier (e.g., "soil-carbon-v1.2.2")
        scope: Optional filter -- "farm" for per-farm requirements,
               "meta" for meta-project requirements, or None for all.

    Returns:
        Full checklist dict with filtered requirements list.

    Raises:
        FileNotFoundError: If the checklist file does not exist.
    """
    checklist_path = settings.get_checklist_path(methodology)
    if not checklist_path.exists():
        raise FileNotFoundError(f"Checklist not found: {checklist_path}")

    with open(checklist_path, "r") as f:
        checklist_data = json.load(f)

    if scope is not None:
        checklist_data["requirements"] = [
            req for req in checklist_data.get("requirements", []) if req.get("scope") == scope
        ]

    return checklist_data


def validate_program_guide_url(url: str | None, timeout: float = 2.0) -> str | None:
    """Soft-validate a checklist's `program_guide_url` field.

    Issues a HEAD request to confirm the canonical methodology page is still
    reachable. Returns a human-readable warning string if the URL is missing,
    times out, or returns a non-2xx status. Returns ``None`` on success.

    This is non-blocking: callers should surface the warning but never abort
    a session start because of it. Network failure is treated the same as a
    soft warning -- the methodology may have been published before the
    reviewer was online.

    Args:
        url: The canonical methodology page URL, or None if not configured.
        timeout: Request timeout in seconds (default 2.0).

    Returns:
        Warning string, or None if URL is reachable / not configured.
    """
    if not url:
        return None
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.status
            if 200 <= status < 300:
                return None
            return f"program_guide_url returned HTTP {status}: {url}"
    except urllib.error.HTTPError as exc:
        return f"program_guide_url returned HTTP {exc.code}: {url}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"program_guide_url unreachable ({exc.__class__.__name__}): {url}"
