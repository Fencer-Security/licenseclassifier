"""The script that produces the shipped scanner.bin.gz.

`python -m licenseclassifier._engine._build` is how the artifact every other test depends on
gets made. It runs only at packaging time, so nothing else here would notice if it broke --
and the failure mode is shipping a wheel whose artifact silently falls back to compiling from
source. It writes to a real path, so these tests redirect that path into tmp_path.
"""

from __future__ import annotations

import gzip
import importlib
import marshal

from licenseclassifier._engine import _build

scan_module = importlib.import_module("licenseclassifier._engine.scan")


def test_build_writes_an_artifact_the_loader_accepts(tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "scanner.bin.gz"
    monkeypatch.setattr(_build, "_ARTIFACT_PATH", artifact)
    monkeypatch.setattr(scan_module, "_ARTIFACT_PATH", artifact)

    _build.main()

    assert artifact.exists()
    assert scan_module._load_prebuilt() is not None, "the loader rejected a freshly built artifact"
    assert "scanner.bin.gz" in capsys.readouterr().out


def test_built_artifact_reproduces_the_source_compiled_scanner(tmp_path, monkeypatch, source_scanner):
    """The build must be a faithful serialization, not a lossy one."""
    artifact = tmp_path / "scanner.bin.gz"
    monkeypatch.setattr(_build, "_ARTIFACT_PATH", artifact)

    _build.main()

    with gzip.open(artifact, "rb") as f:
        built = marshal.load(f)
    expected = source_scanner.compiled_data()
    assert built.keys() == expected.keys()
    assert built["ops"] == expected["ops"]
    assert built["args"] == expected["args"]
    assert built["ids"] == expected["ids"]
    assert built["words"] == expected["words"]


def test_build_is_deterministic(tmp_path, monkeypatch):
    """Two builds from the same sources must produce identical bytes, or every regeneration
    would show up as a spurious diff in review."""
    first, second = tmp_path / "a.bin.gz", tmp_path / "b.bin.gz"
    for path in (first, second):
        monkeypatch.setattr(_build, "_ARTIFACT_PATH", path)
        _build.main()

    # Compare the marshalled payload rather than the gzip container, whose header carries an
    # mtime by default.
    with gzip.open(first, "rb") as f:
        a = f.read()
    with gzip.open(second, "rb") as f:
        b = f.read()
    assert a == b
