"""Keep every declaration of the support window in agreement.

The set of supported Pythons is spelled out in four places that cannot see each other:
``SUPPORTED`` in noxfile.py (what a developer tests), the matrix in .github/workflows/ci.yml
(what CI tests), the smoke-test matrix in .github/workflows/release.yml (what the wheel is
verified on before publishing), and the ``Programming Language :: Python`` classifiers plus
``requires-python`` in pyproject.toml (what users are promised). Dropping 3.10 or adding 3.16
in one place and not the others is silent -- the suite stays green while either shipping an
untested version or quietly testing more than it claims.

Skipped when the repository layout is absent, so the suite still passes when it is run
against an installed wheel rather than a checkout -- which is exactly what release.yml's
smoke-test job does.
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
PYPROJECT = REPO_ROOT / "pyproject.toml"
NOXFILE = REPO_ROOT / "noxfile.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

pytestmark = pytest.mark.skipif(
    not (PYPROJECT.exists() and NOXFILE.exists() and WORKFLOW.exists() and RELEASE_WORKFLOW.exists()),
    reason="not running from a source checkout",
)


def _matrix_versions(path: Path) -> list[str]:
    """The `python-version:` matrix list from a workflow file.

    Read with a regex rather than a YAML parser to keep the test suite dependency-free; each
    of these files has exactly one such matrix, which the caller's assertions rely on.
    """
    text = path.read_text(encoding="utf-8")
    matches = re.findall(r"^\s*python-version:\s*\[(.*?)\]", text, re.MULTILINE)
    assert len(matches) == 1, f"expected exactly one python-version matrix in {path}, found {len(matches)}"
    return re.findall(r'"([\d.]+)"', matches[0])


def _pinned_versions(path: Path) -> list[str]:
    """Every single Python version hard-coded in a workflow, ignoring matrix lists.

    Matches `python-version: "3.14"` and `--python 3.14`. The matrix form
    `python-version: ["3.10", ...]` does not match, because a `[` follows the colon.
    """
    text = path.read_text(encoding="utf-8")
    return re.findall(r'python-version:\s*"(\d+\.\d+)"', text) + re.findall(r"--python (\d+\.\d+)", text)


@pytest.fixture(scope="module")
def nox_versions() -> list[str]:
    """noxfile.py's SUPPORTED, read as text rather than imported -- importing it requires nox."""
    match = re.search(r"^SUPPORTED = \[(.*?)\]", NOXFILE.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
    assert match, f"could not find SUPPORTED in {NOXFILE}"
    return re.findall(r'"([\d.]+)"', match.group(1))


@pytest.fixture(scope="module")
def newest_stable() -> str:
    """noxfile.py's NEWEST_STABLE -- the one interpreter that jobs outside the matrix use."""
    match = re.search(r'^NEWEST_STABLE = "([\d.]+)"', NOXFILE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"could not find NEWEST_STABLE in {NOXFILE}"
    return match.group(1)


@pytest.fixture(scope="module")
def workflow_versions() -> list[str]:
    """The matrix the CI test job runs the suite on."""
    return _matrix_versions(WORKFLOW)


@pytest.fixture(scope="module")
def release_workflow_versions() -> list[str]:
    """The matrix the release smoke-test job installs the built wheel on."""
    return _matrix_versions(RELEASE_WORKFLOW)


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def classifier_versions(pyproject: dict) -> list[str]:
    prefix = "Programming Language :: Python :: "
    return [
        c.removeprefix(prefix)
        for c in pyproject["project"]["classifiers"]
        if c.startswith(prefix) and c != prefix + "3 :: Only"
    ]


def test_ci_matrix_matches_the_noxfile(nox_versions, workflow_versions):
    assert workflow_versions == nox_versions


def test_release_smoke_test_matrix_matches_the_noxfile(nox_versions, release_workflow_versions):
    """A version missing here would be published without the wheel ever being installed and
    exercised on it."""
    assert release_workflow_versions == nox_versions


@pytest.mark.parametrize("workflow", [WORKFLOW, RELEASE_WORKFLOW], ids=lambda p: p.name)
def test_every_uv_invocation_names_an_interpreter(workflow):
    """`uv run` with no `--python` silently uses whatever uv resolves by default. That is how
    CI ended up reporting a different statement total than a local run: coverage parses the
    source with the interpreter running it, and the count differs by version. Every invocation
    has to say which interpreter it means, whether that is the matrix variable or a literal.
    """
    offenders = [
        line.strip()
        for line in workflow.read_text(encoding="utf-8").splitlines()
        if re.search(r"\buv (run|venv)\b", line) and "--python" not in line
    ]
    assert not offenders, f"{workflow.name} has uv invocations without --python: {offenders}"


@pytest.mark.parametrize("workflow", [WORKFLOW, RELEASE_WORKFLOW], ids=lambda p: p.name)
def test_single_version_jobs_all_pin_newest_stable(workflow, newest_stable):
    """Jobs outside the matrix -- lint, build, the coverage report -- must all pin the same
    interpreter, and it must be the newest *stable* one so release artifacts are never built by
    a prerelease.

    This is not only tidiness for the coverage job: `coverage` counts statements by parsing the
    source with whatever interpreter runs the report, and that count differs across versions, so
    an unpinned or mismatched reporter makes CI's totals disagree with a local run.
    """
    pinned = _pinned_versions(workflow)
    assert pinned, f"expected at least one pinned python version in {workflow.name}"
    assert set(pinned) == {newest_stable}, (
        f"{workflow.name} pins {sorted(set(pinned))}, expected only NEWEST_STABLE {newest_stable!r}"
    )


def test_newest_stable_is_a_supported_version_and_not_the_prerelease(nox_versions, newest_stable):
    """It has to be in the support window, and it must not be the newest entry, which is the
    prerelease this project deliberately tests but does not release from."""
    assert newest_stable in nox_versions
    assert newest_stable != nox_versions[-1], (
        f"NEWEST_STABLE {newest_stable!r} is the newest supported version, which is the "
        f"prerelease; it should be the one before it"
    )


def test_classifiers_match_the_noxfile(nox_versions, classifier_versions):
    assert classifier_versions == nox_versions


def test_requires_python_floor_is_the_oldest_supported_version(pyproject, nox_versions):
    assert pyproject["project"]["requires-python"] == f">={nox_versions[0]}"


def test_versions_are_listed_oldest_first_with_no_gaps(nox_versions):
    parsed = [tuple(int(p) for p in v.split(".")) for v in nox_versions]
    assert parsed == sorted(parsed), "list newest last"
    assert all(major == 3 for major, _ in parsed), "only Python 3 is supported"
    minors = [minor for _, minor in parsed]
    assert minors == list(range(minors[0], minors[-1] + 1)), "the supported range must be contiguous"


def test_running_interpreter_is_a_supported_version(nox_versions):
    """A matrix entry that silently resolved to the wrong interpreter would otherwise look
    like a passing run."""
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert running in nox_versions
