"""Pure-Python reimplementation of the license-identification algorithm from
github.com/google/licensecheck (v0.3.1).

Identifies SPDX licenses in free-form license text by matching against license
patterns written in that reference implementation's LRE syntax. Verified to full
parity (matched IDs and coverage percent) against all 672 of its testdata fixtures.

The patterns (licenses.json.gz) are built from the corpus sources in data/: 423 of
them are vendored from google/licensecheck v0.3.1, the rest were generated from the
SPDX License List matching templates by this project. The compiled scanner
(scanner.bin.gz) is prebuilt from the patterns by
`python -m licenseclassifier._engine._build`. This package is pure Python.
"""

from licenseclassifier._engine.scan import Coverage, Match, Scanner, builtin_scanner, scan

__all__ = ["Coverage", "Match", "Scanner", "builtin_scanner", "scan"]
