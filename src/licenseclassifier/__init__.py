"""Pure-Python SPDX license identification from license text.

See https://github.com/Fencer-Security/licenseclassifier for documentation.
"""

from licenseclassifier.license_identifier import (
    COVERAGE_THRESHOLD,
    LicenseIdentificationResult,
    identify_license,
)

__all__ = ["COVERAGE_THRESHOLD", "LicenseIdentificationResult", "identify_license"]

# CalVer, YYYY.MM.MICRO. This literal is the single source of truth: pyproject.toml reads it
# via [tool.setuptools.dynamic]. Do not zero-pad the month -- PEP 440 would normalise it away.
__version__ = "2026.7.0"
