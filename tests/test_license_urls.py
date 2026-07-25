"""Recognising a licence from a URL reference rather than from its text.

A great many files say "Licensed under the Apache License, Version 2.0; see
http://www.apache.org/licenses/LICENSE-2.0" and never include the licence body. The scanner
maps such URLs to SPDX IDs after normalising away the scheme, a trailing slash, Creative
Commons' /legalcode suffix, and -- failing an exact hit -- one trailing path segment.
"""

from __future__ import annotations

import pytest

APACHE = "www.apache.org/licenses/LICENSE-2.0"


class TestUrlNormalisation:
    @pytest.mark.parametrize(
        "url",
        [
            f"http://{APACHE}",
            f"https://{APACHE}",
            f"http://{APACHE}/",
            APACHE,
        ],
    )
    def test_scheme_and_trailing_slash_are_ignored(self, scanner, url):
        assert scanner._license_url(url) == "Apache-2.0"

    def test_host_and_path_matching_is_case_insensitive(self, scanner):
        assert scanner._license_url(f"http://{APACHE.upper()}") == "Apache-2.0"

    @pytest.mark.parametrize("scheme", ["HTTP://", "Http://", "HTTPS://", "Https://"])
    def test_an_uppercase_scheme_is_not_recognised(self, scanner, scheme):
        """Characterization, not endorsement. The scheme is stripped with a case-sensitive
        prefix removal *before* the URL is lowercased, so only a lowercase scheme is
        stripped; the leftover "http://" then fails to match any key. Meanwhile the URL
        regexp that finds these references in text is case-insensitive and happily matches
        "HTTP://", so a licence reference written with an uppercase scheme is found and then
        silently not resolved.

        This mirrors google/licensecheck, whose strings.TrimPrefix is likewise
        case-sensitive, so it is pinned rather than fixed: this package's headline claim is
        parity with that implementation, and diverging here would break it. It is worth
        raising upstream.
        """
        assert scanner._license_url(f"{scheme}{APACHE}") is None

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://creativecommons.org/licenses/by/4.0", "CC-BY-4.0"),
            ("https://creativecommons.org/licenses/by/4.0/", "CC-BY-4.0"),
            ("https://creativecommons.org/licenses/by/4.0/legalcode", "CC-BY-4.0"),
        ],
    )
    def test_creative_commons_legalcode_suffix_is_stripped(self, scanner, url, expected):
        assert scanner._license_url(url) == expected

    def test_one_unrecognised_trailing_segment_is_dropped(self, scanner):
        """The fallback that lets a deep link still resolve to its licence."""
        assert scanner._license_url(f"http://{APACHE}/anchor") == "Apache-2.0"

    def test_two_unrecognised_trailing_segments_are_not_dropped(self, scanner):
        """Only one segment is trimmed, so the fallback cannot wander up to an unrelated
        licence several levels above."""
        assert scanner._license_url(f"http://{APACHE}/anchor/deeper") is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.org/not/a/license",
            "http://www.apache.org",
            "",
        ],
    )
    def test_unknown_urls_resolve_to_nothing(self, scanner, url):
        assert scanner._license_url(url) is None


class TestUrlMatchesInText:
    def test_url_reference_is_reported_as_a_url_match(self, scanner):
        coverage = scanner.scan("Licensed under https://www.apache.org/licenses/LICENSE-2.0")
        (match,) = coverage.match
        assert (match.id, match.is_url) == ("Apache-2.0", True)

    def test_url_match_offsets_span_exactly_the_url(self, scanner):
        text = "Licensed under https://www.apache.org/licenses/LICENSE-2.0 and nothing else"
        (match,) = scanner.scan(text).match
        assert text[match.start : match.end] == "https://www.apache.org/licenses/LICENSE-2.0"

    def test_license_text_matches_are_not_flagged_as_urls(self, scanner, license_text):
        (match,) = scanner.scan(license_text("apache-license-2.0")).match
        assert match.is_url is False

    def test_a_non_license_url_contributes_no_match(self, scanner):
        assert scanner.scan("See https://example.org/docs/readme for details").match == []
