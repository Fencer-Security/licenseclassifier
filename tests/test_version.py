"""The version is CalVer, and there is only one place it is written down.

`licenseclassifier.__version__` is the sole declaration; pyproject.toml reads it through
`[tool.setuptools.dynamic]`. Two things can go wrong quietly and both are caught here: a
release numbered with SemVer out of habit, and a version string that PEP 440 silently rewrites
during the build so the published filename no longer matches the git tag that produced it.
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

import licenseclassifier

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# YYYY.MM.MICRO -- four-digit year, unpadded month, release counter within the month.
CALVER = re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{1,2})\.(?P<micro>\d+)$")

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def test_version_is_calver():
    assert CALVER.match(licenseclassifier.__version__), (
        f"{licenseclassifier.__version__!r} is not CalVer YYYY.MM.MICRO -- see the Versioning section of CHANGELOG.md"
    )


def test_month_is_a_real_month():
    month = int(CALVER.match(licenseclassifier.__version__)["month"])
    assert 1 <= month <= 12


def test_year_is_plausible():
    """Catches a transposed or truncated year, which would sort wrongly on PyPI forever."""
    year = int(CALVER.match(licenseclassifier.__version__)["year"])
    assert 2024 <= year <= 2100


def test_month_is_not_zero_padded():
    """PEP 440 normalises "2026.07.0" to "2026.7.0", so a padded month would make the built
    filename disagree with the version in the source and with the release tag."""
    month = CALVER.match(licenseclassifier.__version__)["month"]
    assert not month.startswith("0"), f"month {month!r} is zero-padded"


def test_version_is_already_pep440_canonical():
    """The general form of the check above: whatever the string is, the build must not rename
    it. Skipped rather than vendored -- `packaging` is a build-time tool, not a test dependency."""
    packaging_version = pytest.importorskip("packaging.version")
    assert str(packaging_version.Version(licenseclassifier.__version__)) == licenseclassifier.__version__


def test_installed_metadata_matches_the_module():
    """Confirms pyproject.toml's dynamic lookup actually resolves to `__version__`. A stale
    result here means the package needs reinstalling, which `nox` and CI always do."""
    assert installed_version("licenseclassifier") == licenseclassifier.__version__


@pytest.mark.skipif(not PYPROJECT.exists(), reason="not running from a source checkout")
def test_pyproject_declares_the_version_dynamically():
    """Guards the single-source arrangement: re-adding a static `version` would reintroduce the
    drift this file exists to prevent, and the tests above would still pass."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    assert "version" in project.get("dynamic", []), "expected version to be declared dynamic"
    assert "version" not in project, "version must not also be set statically"
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "licenseclassifier.__version__"}


@pytest.mark.skipif(not (REPO_ROOT / "CHANGELOG.md").exists(), reason="not running from a source checkout")
def test_changelog_has_a_section_for_the_current_version():
    """A release whose changelog entry was never written is the normal way this gets forgotten."""
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{licenseclassifier.__version__}]" in changelog, (
        f"CHANGELOG.md has no '## [{licenseclassifier.__version__}]' section"
    )


@pytest.mark.skipif(not (REPO_ROOT / "CHANGELOG.md").exists(), reason="not running from a source checkout")
def test_changelog_does_not_promise_semver():
    """It used to. Leaving that claim in place while shipping CalVer would misrepresent the
    compatibility guarantee to anyone pinning a range."""
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "semver.org" not in changelog
    assert "Semantic Versioning" not in changelog
