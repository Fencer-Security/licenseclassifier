"""How a match's reported region is widened, and when a URL is credited to a licence.

`Scanner.scan` does not report the raw word span the matcher found. It snaps the region out to
line boundaries so a caller slicing the text gets whole lines, back-fills a preceding copyright
line into the region, and separately credits bare licence URLs found between matches. Those
adjustments are what make the offsets useful, and they are invisible through
`identify_license`'s pass/fail result.
"""

from __future__ import annotations

import pytest

from licenseclassifier._engine.scan import MAX_COPYRIGHT_WORDS


@pytest.fixture(scope="module")
def mit(license_text) -> str:
    text = license_text("multi-license")
    start = text.index("Permission is hereby granted, free of charge")
    return text[start : start + 1100]


class TestRegionSnapsToLineBoundaries:
    def test_a_preceding_word_on_the_same_line_does_not_extend_the_region(self, scanner, mit):
        """With no newline between the previous word and the licence, there is no line start
        to snap back to, so the region begins at the licence's first word."""
        text = "Note: " + mit
        (match,) = scanner.scan(text).match
        assert text[match.start :].startswith("Permission is hereby")

    def test_a_licence_starting_on_its_own_line_snaps_back_to_the_line_start(self, scanner, mit):
        """The region is widened to include the line's leading whitespace, so slicing it
        reproduces the licence as it was laid out rather than mid-line."""
        text = "Note:\n    " + mit
        (match,) = scanner.scan(text).match
        assert text[match.start :].startswith("    Permission is hereby")

    def test_a_licence_at_offset_zero_starts_at_zero(self, scanner, mit):
        (match,) = scanner.scan(mit).match
        assert match.start == 0

    def test_a_licence_ending_the_text_ends_at_the_text_length(self, scanner, mit):
        (match,) = scanner.scan(mit).match
        assert match.end == len(mit)

    def test_a_trailing_line_is_closed_at_its_newline(self, scanner, mit):
        """When text follows the licence, the region ends just past the newline that ends the
        licence's last line, not in the middle of it."""
        text = mit + "\nUnrelated trailing prose that is not part of the licence.\n"
        (match,) = scanner.scan(text).match
        assert text[match.end - 1] == "\n"
        assert "Unrelated trailing prose" not in text[: match.end]


class TestCopyrightBackfill:
    def test_a_preceding_copyright_line_is_pulled_into_the_region(self, scanner, mit):
        """A licence file's copyright line is part of the licence for reporting purposes even
        though it is not part of the matched pattern."""
        text = "Copyright (c) 2026 Fencer Security\n\n" + mit
        (match,) = scanner.scan(text).match
        assert text[match.start :].startswith("Copyright (c) 2026")

    def test_backfill_reaches_no_further_than_the_word_limit(self, scanner, mit):
        """Bounded by MAX_COPYRIGHT_WORDS, so an unrelated copyright notice far above the
        licence does not swallow everything in between."""
        filler = "filler " * (MAX_COPYRIGHT_WORDS + 10)
        text = f"Copyright (c) 2026 Someone Else\n\n{filler}\n\n{mit}"
        (match,) = scanner.scan(text).match
        assert "Copyright (c) 2026 Someone Else" not in text[match.start : match.end]


class TestUrlDetectionEdges:
    def test_the_bare_word_http_is_not_treated_as_a_url(self, scanner):
        """ "http" is in the dictionary as a word in its own right, so every occurrence is
        offered to the URL matcher; prose that merely mentions it must yield nothing."""
        assert scanner.scan("http is a protocol").match == []

    def test_a_real_url_at_the_same_position_is_recognised(self, scanner):
        """The contrast case for the test above: the difference is the URL, not the word."""
        (match,) = scanner.scan("http://www.apache.org/licenses/LICENSE-2.0 is a licence").match
        assert (match.id, match.is_url) == ("Apache-2.0", True)

    def test_a_url_overlapping_the_following_match_is_not_credited_separately(self, scanner, mit):
        """When a URL's text runs into the words that begin a licence match, the licence is
        reported from its text rather than counted twice as a URL reference."""
        text = "https://example.org/permission " + mit[len("Permission ") :]
        (match,) = scanner.scan(text).match
        assert (match.id, match.is_url) == ("MIT", False)
