"""Tolerance for licence text that is not byte-identical to the pattern.

Real licence files are retyped, OCR'd, reflowed and hand-edited, so the matcher accepts a
one-character misspelling per word, a pattern word split across two input words, and two
pattern words run together. That tolerance is the difference between recognising a licence
file and not, and none of the verbatim fixtures exercise it.

The end-to-end cases corrupt exactly one occurrence, so a failure means the tolerance rule
broke rather than that the input was mangled past recognition.
"""

from __future__ import annotations

import pytest

from licenseclassifier._engine.matcher import _can_misspell, _can_misspell_join


@pytest.fixture(scope="module")
def mit(license_text) -> str:
    """The verbatim MIT licence, which identifies cleanly before any corruption."""
    text = license_text("multi-license")
    start = text.index("Permission is hereby granted, free of charge")
    return text[start : start + 1100]


def test_the_uncorrupted_baseline_identifies(identify, mit):
    """Guards every other test in this module: if the baseline stopped matching, the
    corruption cases below would pass for the wrong reason."""
    assert [r.id for r in identify(mit)] == ["MIT"]


class TestSingleCharacterMisspellings:
    @pytest.mark.parametrize(
        ("original", "corrupted", "kind"),
        [
            ("WARRANTIES", "WARRENTIES", "substitution"),
            ("permission", "permision", "deletion"),
            ("notice", "noticce", "insertion"),
        ],
    )
    def test_one_typo_in_a_long_word_still_identifies(self, identify, mit, original, corrupted, kind):
        assert original in mit
        assert [r.id for r in identify(mit.replace(original, corrupted, 1))] == ["MIT"], kind

    @pytest.mark.parametrize(
        ("want", "have"),
        [
            ("license", "licence"),  # substitution
            ("copyright", "copyrigh"),  # trailing deletion
            ("copyright", "ccopyright"),  # leading insertion
            ("abcd", "abxd"),
        ],
    )
    def test_can_misspell_accepts_one_edit(self, want, have):
        assert _can_misspell(want, have)

    @pytest.mark.parametrize(
        ("want", "have", "why"),
        [
            ("the", "teh", "both words shorter than four characters"),
            ("abcd", "axyd", "two separate edits"),
            ("license", "permission", "length differs by more than one"),
            ("abcd", "wxyz", "nothing in common"),
        ],
    )
    def test_can_misspell_rejects(self, want, have, why):
        assert not _can_misspell(want, have), why

    @pytest.mark.parametrize("have", ["c", "copyright", "©"])
    @pytest.mark.parametrize("want", ["c", "copyright"])
    def test_copyright_spellings_are_mutually_interchangeable(self, want, have):
        """A special case in the matcher, not a consequence of the edit distance: "c" and
        "copyright" differ by far more than one character."""
        assert _can_misspell(want, have)


class TestWordBoundaryDamage:
    @pytest.mark.parametrize(
        ("original", "corrupted"),
        [
            ("free of charge", "freeof charge"),
            ("to any person", "toany person"),
            ("of the Software", "ofthe Software"),
        ],
    )
    def test_two_words_run_together_still_identifies(self, identify, mit, original, corrupted):
        assert original in mit
        assert [r.id for r in identify(mit.replace(original, corrupted, 1))] == ["MIT"]

    @pytest.mark.parametrize(
        ("original", "corrupted"),
        [
            ("without restriction", "with out restriction"),
            ("the Software", "the Soft ware"),
            ("including without", "includ ing without"),
        ],
    )
    def test_one_word_split_in_two_still_identifies(self, identify, mit, original, corrupted):
        assert original in mit
        assert [r.id for r in identify(mit.replace(original, corrupted, 1))] == ["MIT"]

    @pytest.mark.parametrize(
        ("want", "have1", "have2"),
        [
            ("software", "soft", "ware"),
            ("copyright", "copy", "right"),
            ("including", "includ", "ing"),
        ],
    )
    def test_can_misspell_join_accepts_an_exact_split(self, want, have1, have2):
        assert _can_misspell_join(want, have1, have2)

    @pytest.mark.parametrize(
        ("want", "have1", "have2", "why"),
        [
            ("software", "sof", "ware", "a character went missing at the split"),
            ("software", "soft", "wares", "an extra character at the end"),
            ("software", "ware", "soft", "the halves are in the wrong order"),
        ],
    )
    def test_can_misspell_join_requires_an_exact_split(self, want, have1, have2, why):
        assert not _can_misspell_join(want, have1, have2), why


class TestToleranceIsBounded:
    """The flip side of the tests above: the fuzziness has a hard limit, so a rewritten
    licence is not silently reported as the original."""

    def test_two_edits_in_a_single_word_breaks_the_match(self, identify, mit):
        """One edit per word is the whole budget, and the matcher is leftmost-longest, so a
        single unrecognisable word in the middle truncates the match below the threshold."""
        assert "WARRANTIES" in mit
        assert identify(mit.replace("WARRANTIES", "WXRRXNTIES", 1)) == []

    def test_a_paraphrase_is_not_reported_as_the_license(self, identify):
        assert identify("You may do whatever you like with this code. No warranty of any kind.") == []
