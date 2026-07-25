from dataclasses import dataclass

from licenseclassifier._engine import scan

# Matches licensecheck's DefaultCoverageThreshold: only report evidence when the
# recognised licence text covers at least this percentage of the input.
COVERAGE_THRESHOLD = 75.0


@dataclass(frozen=True)
class LicenseIdentificationResult:
    """One matched license region within the scanned text."""

    id: str
    """SPDX license identifier, e.g. "Apache-2.0"."""

    start: int
    """Character offset of the first character of the match."""

    end: int
    """Character offset one past the last character of the match."""


def identify_license(
    license_text: str, coverage_threshold: float = COVERAGE_THRESHOLD
) -> list[LicenseIdentificationResult]:
    """Identify the SPDX licenses present in ``license_text``.

    Returns one result per matched license region, in the order the regions appear in
    the text. The same license ID can appear more than once when the text contains it
    more than once (a bundled THIRD_PARTY file, say).

    Returns an empty list when the matched regions together cover less than
    ``coverage_threshold`` percent of the input. That guard is what keeps the scanner
    from reporting a license for a file that merely mentions one: a README with an
    inlined MIT paragraph is mostly prose, so it falls below the threshold and reports
    nothing. Lower it to accept partial matches, raise it to demand a near-verbatim
    license file.
    """
    coverage = scan(license_text)
    if coverage.percent < coverage_threshold:
        return []
    return [LicenseIdentificationResult(id=m.id, start=m.start, end=m.end) for m in coverage.match]
