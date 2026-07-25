"""Pure-Python reimplementation of the license-identification algorithm from
github.com/google/licensecheck (v0.3.1).

Identifies SPDX licenses in free-form license text by matching against the same
built-in license patterns that reference implementation uses. Verified to full
parity (matched IDs and coverage percent) against all 672 of its testdata fixtures.

The license patterns (licenses.json.gz) are vendored data extracted from
google/licensecheck v0.3.1. The compiled scanner (scanner.bin.gz) is prebuilt from
them by `python -m licenseclassifier._engine._build`. This package is pure Python.
"""

from licenseclassifier._engine.scan import Coverage, Match, Scanner, builtin_scanner, scan

__all__ = ["Coverage", "Match", "Scanner", "builtin_scanner", "scan"]
