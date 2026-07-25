"""RELEASING.md must describe the release workflow that actually exists.

The Trusted Publisher settings in RELEASING.md are transcribed by hand into a form on PyPI, and
they have to match `.github/workflows/release.yml` exactly or uploads are rejected with an
authentication error that says nothing about the mismatch. Nobody re-reads the doc when they
rename a workflow, so the doc is checked against the workflow here.

Skipped when the repository layout is absent, so a run against an installed wheel still passes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASING = REPO_ROOT / "RELEASING.md"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

pytestmark = pytest.mark.skipif(
    not (RELEASING.exists() and RELEASE_WORKFLOW.exists() and PYPROJECT.exists()),
    reason="not running from a source checkout",
)


@pytest.fixture(scope="module")
def releasing() -> str:
    return RELEASING.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def documented_publisher(releasing: str) -> dict[str, str]:
    """The `| Field | Value |` rows of the Trusted Publisher setup table.

    Leading whitespace is allowed: the table sits inside a numbered list, so its rows are indented.
    """
    rows = re.findall(r"^[ \t]*\|\s*([^|]+?)\s*\|\s*`([^`|]+)`\s*\|\s*$", releasing, re.MULTILINE)
    settings = {field: value for field, value in rows}
    for required in ("Owner", "Repository name", "Workflow name", "Environment name"):
        assert required in settings, f"RELEASING.md has no '{required}' row in the publisher table"
    return settings


def test_documented_workflow_filename_exists(documented_publisher):
    assert documented_publisher["Workflow name"] == RELEASE_WORKFLOW.name


def test_documented_environment_matches_the_publish_job(documented_publisher):
    """PyPI keys the trust relationship on the environment name; a mismatch fails the upload."""
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    environments = re.findall(r"^\s*environment:\s*(\S+)\s*$", workflow, re.MULTILINE)
    assert environments == [documented_publisher["Environment name"]], (
        f"RELEASING.md documents environment {documented_publisher['Environment name']!r} "
        f"but {RELEASE_WORKFLOW.name} declares {environments}"
    )


def test_documented_owner_and_repository_match_the_project_urls(documented_publisher):
    """Guards against the doc being copied from another project."""
    urls = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["urls"]
    expected = f"{documented_publisher['Owner']}/{documented_publisher['Repository name']}"
    assert urls["Source"].endswith(expected), f"{urls['Source']} does not end with {expected}"


def test_publish_job_requests_an_oidc_token(documented_publisher):
    """Trusted publishing needs `id-token: write`; without it the upload falls back to looking
    for a password that does not exist, which is a confusing way to find out."""
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^\s*id-token:\s*write\s*$", workflow, re.MULTILINE)


def test_releasing_doc_warns_about_zero_padded_months(releasing):
    """The one mistake in this scheme that produces a silently renamed artifact."""
    assert "zero-pad" in releasing.lower()


def test_releasing_doc_is_linked_from_the_readme():
    """An unlinked runbook is one nobody finds."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "RELEASING.md" in readme
