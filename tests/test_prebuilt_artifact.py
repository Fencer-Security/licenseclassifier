"""Integrity of the shipped scanner.bin.gz on the interpreter running the suite.

This module is the main reason the suite is worth running on every supported Python. The
artifact is serialized with ``marshal``, whose format the stdlib explicitly does not promise
to keep portable across versions, and it is built once (by whichever interpreter ran
``_build``) and then shipped to all of them. ``_load_prebuilt`` swallows every load error and
falls back to compiling from source, so an artifact that has stopped deserializing on, say,
3.10 produces no error and no wrong answers -- just a silently much slower import. Only an
explicit assertion on the loaded artifact catches that.
"""

from __future__ import annotations

import gzip
import importlib
import marshal

import pytest

from licenseclassifier._engine.scan import (
    _ARTIFACT_PATH,
    FORMAT_VERSION,
    Scanner,
    builtin_scanner,
)

# See the note in conftest.py: the `scan` function shadows the `scan` submodule on the
# package, and these tests patch that module's globals.
scan_module = importlib.import_module("licenseclassifier._engine.scan")

FIXTURES = ["apache-license-2.0", "multi-license"]


@pytest.fixture
def committed_artifact() -> dict:
    with gzip.open(_ARTIFACT_PATH, "rb") as f:
        return marshal.load(f)


def test_artifact_deserializes_on_this_interpreter(prebuilt_scanner):
    """Guarded by the fixture, which fails loudly instead of skipping."""
    assert prebuilt_scanner.ids
    assert prebuilt_scanner.urls
    assert prebuilt_scanner.dict.list


def test_artifact_declares_the_current_format_version(committed_artifact):
    assert committed_artifact["version"] == FORMAT_VERSION


def test_artifact_is_regenerated_from_the_current_sources(committed_artifact, source_scanner):
    """Fails when licenses.json.gz or the compiler changed without re-running
    `python -m licenseclassifier._engine._build`."""
    assert _normalize(committed_artifact) == _normalize(source_scanner.compiled_data())


def test_artifact_survives_a_marshal_round_trip_here(source_scanner):
    """Isolates 'this interpreter cannot read the committed bytes' from 'this interpreter
    cannot round-trip the format at all' -- the second points at marshal, the first at a
    stale artifact."""
    fresh = source_scanner.compiled_data()
    assert _normalize(marshal.loads(marshal.dumps(fresh))) == _normalize(fresh)


def test_default_scanner_uses_the_prebuilt_artifact(monkeypatch):
    """builtin_scanner() must not be quietly taking the compile-from-source fallback."""
    monkeypatch.setattr(scan_module, "_builtin", None)
    calls: list[str] = []
    real_build = scan_module.build_from_source
    monkeypatch.setattr(scan_module, "build_from_source", lambda: calls.append("built") or real_build())

    assert isinstance(builtin_scanner(), Scanner)
    assert calls == [], "builtin_scanner() fell back to compiling from source"


def test_builtin_scanner_is_cached(monkeypatch):
    monkeypatch.setattr(scan_module, "_builtin", None)
    assert builtin_scanner() is builtin_scanner()


@pytest.mark.parametrize("name", FIXTURES)
def test_prebuilt_and_source_scanners_agree(prebuilt_scanner, source_scanner, license_text, name):
    """The prebuilt artifact is a serialization of the compiler's output, so the two must be
    behaviourally indistinguishable -- including the coverage percentage, which the public
    API reduces to a boolean and would otherwise not pin down."""
    text = license_text(name)
    prebuilt, source = prebuilt_scanner.scan(text), source_scanner.scan(text)
    assert prebuilt.percent == pytest.approx(source.percent)
    assert prebuilt.match == source.match


def test_prebuilt_and_source_word_dictionaries_agree(prebuilt_scanner, source_scanner):
    """from_compiled() rebuilds the word dictionary's index map from the word list; a
    mismatch here would decode every match to the wrong licence."""
    assert prebuilt_scanner.dict.list == source_scanner.dict.list
    assert prebuilt_scanner.dict.lookup("copyright") == source_scanner.dict.lookup("copyright")
    assert prebuilt_scanner.dict.lookup("http") == source_scanner.dict.lookup("http")


@pytest.mark.parametrize(
    ("field", "corruption"),
    [
        ("version", FORMAT_VERSION + 1),
        ("ops", b"\x00\x01\x02"),
        ("words", None),
    ],
)
def test_unusable_artifact_falls_back_instead_of_raising(tmp_path, monkeypatch, committed_artifact, field, corruption):
    """The fallback is the library's safety net for exactly the cross-version marshal break
    this module is watching for, so it needs to actually work."""
    committed_artifact[field] = corruption
    corrupted = tmp_path / "scanner.bin.gz"
    with gzip.open(corrupted, "wb") as f:
        marshal.dump(committed_artifact, f)

    monkeypatch.setattr(scan_module, "_ARTIFACT_PATH", corrupted)
    assert scan_module._load_prebuilt() is None


def test_missing_artifact_falls_back_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_module, "_ARTIFACT_PATH", tmp_path / "absent.bin.gz")
    assert scan_module._load_prebuilt() is None


def _normalize(data: dict) -> dict:
    """Compare compiled programs by value: the on-disk form uses sets and arrays whose
    iteration order is not part of the format."""
    return {
        "version": data["version"],
        "ops": data["ops"],
        "args": data["args"],
        "start_state": list(data["start_state"]),
        "ids": list(data["ids"]),
        "urls": dict(data["urls"]),
        "words": list(data["words"]),
        "start": sorted(tuple(p) for p in data["start"]),
    }
