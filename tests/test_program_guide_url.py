"""Tests for the program_guide_url field plumbing.

Covers:
- The bundled soil-carbon-v1.2.2 checklist exposes program_guide_url.
- The Checklist Pydantic model accepts and round-trips program_guide_url.
- validate_program_guide_url returns None for empty input, None for 2xx,
  and a warning string for non-2xx / network errors / unreachable hosts.

All HTTP behaviour is mocked via urllib.request.urlopen so the suite stays
in the fast pytest band (no real network calls).
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

import pytest

from registry_review_mcp.models.schemas import Checklist
from registry_review_mcp.utils.checklist import (
    load_checklist,
    validate_program_guide_url,
)


# ---------------------------------------------------------------------------
# Bundled checklist exposes the URL
# ---------------------------------------------------------------------------


def test_bundled_soil_carbon_checklist_exposes_program_guide_url() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    assert "program_guide_url" in data
    url = data["program_guide_url"]
    assert isinstance(url, str)
    assert url.startswith("https://www.registry.regen.network/methodology/")


def test_bundled_checklist_round_trips_through_pydantic_model() -> None:
    data = load_checklist("soil-carbon-v1.2.2")
    checklist = Checklist.model_validate(data)
    assert checklist.program_guide_url == data["program_guide_url"]
    # Field is optional and defaults to None when absent.
    minimal = Checklist.model_validate(
        {
            "methodology_id": "x-v0.1",
            "methodology_name": "X",
            "version": "0.1",
            "protocol": "X",
            "program_guide_version": "1.0",
            "requirements": [],
        }
    )
    assert minimal.program_guide_url is None


# ---------------------------------------------------------------------------
# validate_program_guide_url
# ---------------------------------------------------------------------------


@contextmanager
def _mock_urlopen(status: int) -> Iterator[None]:
    """Patch urlopen to return a context-manager response with the given status."""

    class _Response:
        def __init__(self, code: int) -> None:
            self.status = code

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fake_urlopen(_req, timeout=None):  # noqa: ARG001
        return _Response(status)

    with patch("registry_review_mcp.utils.checklist.urllib.request.urlopen", side_effect=fake_urlopen):
        yield


def test_validate_returns_none_for_missing_url() -> None:
    assert validate_program_guide_url(None) is None
    assert validate_program_guide_url("") is None


def test_validate_returns_none_for_2xx() -> None:
    with _mock_urlopen(200):
        assert validate_program_guide_url("https://example.test/page") is None


@pytest.mark.parametrize("status", [301, 404, 500, 503])
def test_validate_warns_on_non_2xx(status: int) -> None:
    with _mock_urlopen(status):
        warning = validate_program_guide_url("https://example.test/page")
    assert warning is not None
    assert str(status) in warning
    assert "https://example.test/page" in warning


def test_validate_handles_http_error() -> None:
    import urllib.error

    def fake_urlopen(_req, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url="https://example.test/page",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    with patch("registry_review_mcp.utils.checklist.urllib.request.urlopen", side_effect=fake_urlopen):
        warning = validate_program_guide_url("https://example.test/page")
    assert warning is not None
    assert "403" in warning


def test_validate_handles_url_error() -> None:
    import urllib.error

    def fake_urlopen(_req, timeout=None):  # noqa: ARG001
        raise urllib.error.URLError("Name or service not known")

    with patch("registry_review_mcp.utils.checklist.urllib.request.urlopen", side_effect=fake_urlopen):
        warning = validate_program_guide_url("https://example.test/page")
    assert warning is not None
    assert "URLError" in warning
    assert "https://example.test/page" in warning


def test_validate_handles_timeout() -> None:
    def fake_urlopen(_req, timeout=None):  # noqa: ARG001
        raise TimeoutError("operation timed out")

    with patch("registry_review_mcp.utils.checklist.urllib.request.urlopen", side_effect=fake_urlopen):
        warning = validate_program_guide_url("https://example.test/page")
    assert warning is not None
    assert "TimeoutError" in warning
