from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from licenseclassifier import identify_license
from licenseclassifier._engine.scan import Scanner, _load_prebuilt, build_from_source

# _engine/__init__.py re-exports the `scan` function, which shadows the `scan` submodule on
# the package, so `from licenseclassifier._engine import scan` yields the function. Tests
# that patch module globals need the module object itself.
scan_module = importlib.import_module("licenseclassifier._engine.scan")

LICENSES = Path(__file__).parent / "data" / "licenses"
REPO_ROOT = Path(__file__).resolve().parents[1]


def pytest_report_header() -> str:
    """Name the interpreter in the header so a matrix run's logs are self-describing."""
    v = sys.version_info
    stage = "" if v.releaselevel == "final" else f" ({v.releaselevel}{v.serial}, prerelease)"
    return f"interpreter: {sys.implementation.name} {v.major}.{v.minor}.{v.micro}{stage}"


@pytest.fixture(scope="session")
def license_text():
    """Read a fixture from tests/data/licenses by name."""

    def read(name: str) -> str:
        return (LICENSES / name).read_text(encoding="utf-8")

    return read


@pytest.fixture(scope="session")
def prebuilt_scanner() -> Scanner:
    """The scanner deserialized from the shipped scanner.bin.gz artifact.

    Fails rather than skips when the artifact will not load: a missing prebuilt artifact is
    a packaging bug on this interpreter, and the library would silently paper over it by
    recompiling from source.
    """
    scanner = _load_prebuilt()
    if scanner is None:
        pytest.fail(
            "scanner.bin.gz did not load on this interpreter; licenseclassifier would fall "
            "back to compiling from source (~20x slower first scan)"
        )
    return scanner


@pytest.fixture(scope="session")
def source_scanner() -> Scanner:
    """A scanner compiled from licenses.json.gz. Session-scoped: this takes about a second."""
    return build_from_source()


@pytest.fixture(params=["prebuilt", "source"])
def scanner(request: pytest.FixtureRequest) -> Scanner:
    """Both construction paths, so behavioural assertions cover each of them."""
    return request.getfixturevalue(f"{request.param}_scanner")


@pytest.fixture
def identify(scanner: Scanner, monkeypatch: pytest.MonkeyPatch):
    """``identify_license`` pinned to one construction path, exercised through the public API."""
    monkeypatch.setattr(scan_module, "_builtin", scanner)
    return identify_license
