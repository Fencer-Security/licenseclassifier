"""The release notes the workflow publishes come out of CHANGELOG.md, so the extraction has to work.

`tools.changelog` runs once per release, inside the job that creates the GitHub release, and its
failure mode is quiet: a section boundary that is off by one heading publishes either half an
entry or two releases' worth of notes under one version, and nobody re-reads a release page after
the fact. The current version's section is extracted here as well, so a changelog written in a
shape the extractor cannot parse fails on the pull request rather than after the upload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import licenseclassifier

REPO_ROOT = Path(__file__).resolve().parents[1]

# Skipped before the import, not with a mark: `tools/` does not ship in the wheel, so the import
# itself is what fails when the suite runs against an installed package.
if not (REPO_ROOT / "tools" / "changelog.py").exists():
    pytest.skip("not running from a source checkout", allow_module_level=True)

from tools.changelog import CHANGELOG, ChangelogError, main, section

SAMPLE = """# Changelog

## [Unreleased]

## [2026.8.0] - 2026-08-17

### Added

- A thing.

### Changed

- Another thing.

## [2026.7.0] - Unreleased

Initial release.
"""


def test_extracts_the_body_without_the_heading():
    body = section("2026.8.0", SAMPLE)
    assert body.startswith("### Added")
    assert "- A thing." in body


def test_stops_at_the_next_release():
    """The boundary that matters: the next `## ` heading, not the `### ` ones inside."""
    body = section("2026.8.0", SAMPLE)
    assert "### Changed" in body, "a `### ` subheading must not end the section"
    assert "2026.7.0" not in body
    assert "Initial release." not in body


def test_extracts_the_last_section_in_the_file():
    """Nothing follows it, so the "next heading" search comes up empty."""
    assert section("2026.7.0", SAMPLE) == "Initial release."


def test_unknown_version_is_an_error():
    with pytest.raises(ChangelogError, match=r"no '## \[2026\.9\.0\]' section"):
        section("2026.9.0", SAMPLE)


def test_empty_section_is_an_error():
    """An empty body would reach GitHub as a release with no notes, which reads as deliberate."""
    with pytest.raises(ChangelogError, match="is empty"):
        section("Unreleased", SAMPLE)


def test_the_version_being_released_has_notes():
    """The real file, for the version in this checkout -- the case the workflow will hit."""
    body = section(licenseclassifier.__version__, CHANGELOG.read_text(encoding="utf-8"))
    assert len(body) > 100, f"the {licenseclassifier.__version__} section is suspiciously short"


def test_cli_writes_the_section_to_a_file(tmp_path):
    out = tmp_path / "notes.md"
    assert main([licenseclassifier.__version__, "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").strip() == section(
        licenseclassifier.__version__, CHANGELOG.read_text(encoding="utf-8")
    )


def test_cli_fails_loudly_on_a_missing_section(capsys):
    """Exit 1 and an explanation, so the release job stops instead of publishing empty notes."""
    assert main(["1999.1.0"]) == 1
    assert "no '## [1999.1.0]' section" in capsys.readouterr().err
