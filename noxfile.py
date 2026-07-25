"""Run the test suite against every supported interpreter.

    uvx nox                  # every version in SUPPORTED
    uvx nox -s tests-3.10    # one version
    uvx nox -- -k artifact   # arguments after -- go to pytest

uv provides the interpreters, so a version that is not installed locally is downloaded on
first use -- including the 3.15 prerelease. `uv run pytest` alone only ever tests the one
interpreter in .venv, and pytest has no notion of a version matrix, which is what nox is
here for.

SUPPORTED is duplicated in the CI matrix (.github/workflows/ci.yml) and in the Python
classifiers in pyproject.toml, because neither can import this file.
tests/test_supported_versions.py fails if the three ever drift apart.
"""

import shutil
from pathlib import Path

import nox

SUPPORTED = ["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"]

# Lint and build run on one interpreter, and it must not be the prerelease: release
# artifacts should not be produced by an alpha.
NEWEST_STABLE = "3.14"

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["tests"]


@nox.session(python=SUPPORTED)
def tests(session: nox.Session) -> None:
    session.install("-e", ".", "--group", "test")
    # One data file per interpreter, named for the version rather than left to
    # --parallel-mode's hostname.pid.random, so the file that fed a number is identifiable
    # -- both here and as a CI artifact. `coverage combine` reports some of these as
    # "skipped": that is its duplicate-content check, and interpreters that executed exactly
    # the same lines legitimately produce identical data.
    session.run(
        "coverage",
        "run",
        f"--data-file=.coverage.{session.python}",
        "-m",
        "pytest",
        *session.posargs,
    )
    session.notify("coverage")


@nox.session(python=NEWEST_STABLE)
def coverage(session: nox.Session) -> None:
    """Merge the per-interpreter coverage data and report against fail_under.

    Queued automatically by `tests`, so `uvx nox` ends with one combined report. Running a
    single version reports only that version's data, which will usually miss the threshold --
    that is a partial measurement, not a regression.
    """
    session.install("coverage[toml]")
    # A missing interpreter's data lowers the numbers without any other symptom, so say so
    # rather than let a partial measurement read as the real one.
    present = {p.name for p in Path().glob(".coverage.*")}
    missing = [v for v in SUPPORTED if f".coverage.{v}" not in present]
    if missing:
        session.warn(f"no coverage data for {', '.join(missing)} -- this report is partial")
    session.run("coverage", "combine")
    session.run("coverage", "report")


@nox.session(python=NEWEST_STABLE)
def lint(session: nox.Session) -> None:
    session.install("ruff")
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")


@nox.session(python=NEWEST_STABLE)
def build(session: nox.Session) -> None:
    """Build the distributions and check their metadata, as the release job does."""
    session.install("build", "twine")
    shutil.rmtree("dist", ignore_errors=True)
    session.run("python", "-m", "build")
    session.run("twine", "check", "--strict", *sorted(str(p) for p in Path("dist").iterdir()))
