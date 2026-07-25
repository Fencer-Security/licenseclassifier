"""Behaviour of the public API, run against both scanner construction paths.

Every test here takes the ``identify`` fixture rather than importing ``identify_license``
directly, so each assertion runs twice: once against the prebuilt artifact and once against
a scanner compiled from source. A divergence between the two is exactly the kind of bug a
cross-version matrix exists to catch.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from licenseclassifier import COVERAGE_THRESHOLD, LicenseIdentificationResult, identify_license

# Both licences appear more than once in the fixture; the API reports one result per region.
MULTI_LICENSE_IDS = [
    "MIT",
    "NCSA",
    "MIT",
    "Apache-2.0",
    "Zlib",
    "Unlicense",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-2-Clause",
]

# "Licensed under <url>" is 5 words, 4 of which the URL match accounts for -> 80% coverage,
# which clears the default threshold. The CC reference is 10 words for 7 -> 70%, which does
# not. The pair pins both sides of the threshold on the URL-detection path.
APACHE_URL_REFERENCE = "Licensed under https://www.apache.org/licenses/LICENSE-2.0"
CC_URL_REFERENCE = "See http://creativecommons.org/licenses/by/4.0/ for details"


def test_single_license_file_is_identified(identify, license_text):
    assert [r.id for r in identify(license_text("apache-license-2.0"))] == ["Apache-2.0"]


def test_each_license_region_is_reported_in_document_order(identify, license_text):
    results = identify(license_text("multi-license"))
    assert [r.id for r in results] == MULTI_LICENSE_IDS
    assert [r.start for r in results] == sorted(r.start for r in results)


def test_regions_do_not_overlap(identify, license_text):
    results = identify(license_text("multi-license"))
    for earlier, later in itertools.pairwise(results):
        assert earlier.end <= later.start, f"{earlier.id} overlaps {later.id}"


@pytest.mark.parametrize("name", ["apache-license-2.0", "multi-license"])
def test_offsets_are_within_bounds(identify, license_text, name):
    text = license_text(name)
    for result in identify(text):
        assert 0 <= result.start < result.end <= len(text)


def test_verbatim_license_file_span_covers_the_whole_file(identify, license_text):
    text = license_text("apache-license-2.0")
    (result,) = identify(text)
    assert (result.start, result.end) == (0, len(text))


def test_each_span_re_identifies_as_its_own_license(identify, license_text):
    """Slicing the text by a result's offsets must yield that licence and nothing else --
    the strongest available check that the spans are attributed to the right regions."""
    text = license_text("multi-license")
    for result in identify(text):
        assert [r.id for r in identify(text[result.start : result.end])] == [result.id]


def test_offsets_are_character_offsets_not_byte_offsets(identify, license_text):
    """Offsets index the str, not its UTF-8 encoding. Every fixture in this repo is ASCII,
    where the two coincide, so nothing else here would notice the difference.

    The non-ASCII text is on both sides of the licence so the reported span is strictly
    interior: a match that ran to the ends of the input would slice correctly under either
    convention and prove nothing.
    """
    mit = _extract_mit_paragraph(license_text("multi-license"))
    text = "Über die Änderungen — 中文の説明。\n\n" + mit + "\n\nÄhnliche Hinweise — 終わり。\n"
    assert len(text.encode()) > len(text), "fixture must contain multi-byte characters"

    (result,) = identify(text)
    assert result.id == "MIT"
    assert 0 < result.start and result.end < len(text), "span must be interior to be diagnostic"

    span = text[result.start : result.end]
    assert span.startswith("Permission is hereby granted")
    assert "Änderungen" not in span, "span leaked the preamble; offsets look like byte offsets"
    assert "Hinweise" not in span, "span leaked the trailing note"


def test_below_threshold_returns_empty(identify):
    assert identify("MIT") == []


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "12345 !!! ---"])
def test_text_without_recognisable_license_words_returns_empty(identify, text):
    assert identify(text) == []


def test_prose_with_an_embedded_license_is_below_the_default_threshold(identify, license_text):
    """The guard documented on identify_license: a README that quotes a licence is mostly
    prose, so it reports nothing until the caller asks for partial matches."""
    text = "This project is great. " * 80 + "\n\n" + _extract_mit_paragraph(license_text("multi-license"))
    assert identify(text) == []
    assert [r.id for r in identify(text, coverage_threshold=25.0)] == ["MIT"]


def test_url_only_reference_is_identified(identify):
    assert [r.id for r in identify(APACHE_URL_REFERENCE)] == ["Apache-2.0"]


def test_url_only_reference_below_the_default_threshold(identify):
    assert identify(CC_URL_REFERENCE) == []
    assert [r.id for r in identify(CC_URL_REFERENCE, coverage_threshold=50.0)] == ["CC-BY-4.0"]


def test_threshold_is_an_inclusive_lower_bound(identify):
    """70.0 is this input's exact coverage, so it must pass at 70.0 and fail just above."""
    assert [r.id for r in identify(CC_URL_REFERENCE, coverage_threshold=70.0)] == ["CC-BY-4.0"]
    assert identify(CC_URL_REFERENCE, coverage_threshold=70.001) == []


def test_threshold_of_zero_reports_every_match(identify, license_text):
    text = "This project is great. " * 80 + "\n\n" + _extract_mit_paragraph(license_text("multi-license"))
    assert [r.id for r in identify(text, coverage_threshold=0.0)] == ["MIT"]


def test_threshold_above_full_coverage_rejects_even_a_verbatim_license(identify, license_text):
    text = license_text("apache-license-2.0")
    assert [r.id for r in identify(text, coverage_threshold=100.0)] == ["Apache-2.0"]
    assert identify(text, coverage_threshold=100.001) == []


def test_default_threshold_matches_the_exported_constant(identify, license_text):
    text = license_text("multi-license")
    assert identify(text) == identify(text, coverage_threshold=COVERAGE_THRESHOLD)


def test_repeated_calls_are_stable(identify, license_text):
    """The scanner is a process-wide singleton; scanning must not mutate it."""
    text = license_text("multi-license")
    first = identify(text)
    assert identify(text) == first
    assert identify(license_text("apache-license-2.0"))
    assert identify(text) == first


def test_result_is_a_frozen_hashable_dataclass(license_text):
    (result,) = identify_license(license_text("apache-license-2.0"))
    assert isinstance(result, LicenseIdentificationResult)
    assert dataclasses.is_dataclass(result)
    assert hash(result) == hash(LicenseIdentificationResult(result.id, result.start, result.end))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.id = "MIT"  # type: ignore[misc]


def _extract_mit_paragraph(multi_license: str) -> str:
    """The verbatim MIT licence out of the multi-license fixture."""
    start = multi_license.index("Permission is hereby granted, free of charge")
    return multi_license[start : start + 1100]
