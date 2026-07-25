# Changelog

All notable changes to this project are documented here.

## Versioning

This project uses [CalVer](https://calver.org/): **`YYYY.MM.MICRO`**, where `MICRO` counts releases
within a month and restarts at `0`. `2026.7.0` is the first release of July 2026; `2026.7.1` the
second. Months are not zero-padded, because PEP 440 normalises `2026.07.0` to `2026.7.0` and the
published filenames would not match the git tag.

CalVer suits this package because what changes between releases is mostly the vendored SPDX license
data, and its usefulness is a function of how recent it is — something a date says and a
`MAJOR.MINOR.PATCH` number does not.

The trade-off is that the version number carries **no compatibility signal**, so this changelog
carries it instead:

- Breaking changes to the public API (`identify_license`, `LicenseIdentificationResult`,
  `COVERAGE_THRESHOLD`) are called out under a **Removed** or **Changed** heading that starts with
  **BREAKING**.
- Anything scheduled for removal is deprecated for at least two months' worth of releases first,
  and emits a `DeprecationWarning` for that whole period.
- Everything under `licenseclassifier._engine` is private and may change in any release.
- Identification results themselves are not part of the API contract: refreshed license data can
  change which IDs a given blob of text matches, and that is a normal, non-breaking release.

## [Unreleased]

### Changed

- Adopted CalVer (`YYYY.MM.MICRO`); the first release is `2026.7.0` rather than `0.1.0`. The version
  now lives only in `licenseclassifier.__version__`, with `pyproject.toml` reading it from there.
- The test suite is now pytest, run across Python 3.10 through the 3.15 prerelease via `nox` with
  `uv`-provided interpreters (`uvx nox`). CI runs one job per version.
- Added tests for the coverage-threshold boundaries, character-offset correctness on non-ASCII
  input, span attribution, and the integrity of the prebuilt `marshal` artifact on each supported
  interpreter. `marshal` is not guaranteed portable across Python versions, and the runtime
  fallback to compiling from source made a broken artifact silent.
- Extended the suite to the text-canonicalization rules, the typo/word-boundary tolerance, licence
  URL resolution, the licence-pattern DSL and its rejection of malformed patterns, and the
  region-boundary and copyright back-fill logic. Line and branch coverage is now 100%, measured
  across the whole matrix and enforced in CI.
- Added a release workflow that publishes to PyPI via a Trusted Publisher on a `v*` tag, gated on
  the built wheel being installed and run against the full suite on every supported interpreter.

### Roadmap

- Vendor the parity harness against `google/licensecheck`'s 672 testdata fixtures so the accuracy
  claim is reproducible from a clean checkout.
- Expose the raw coverage percentage and URL matches through the public API.

## [2026.7.0] - Unreleased

Initial release.

- `identify_license(text, coverage_threshold=75.0)` returns the SPDX license IDs present in a blob
  of license text, one result per matched region, with character offsets.
- 423 SPDX license patterns plus license-URL recognition, vendored from `google/licensecheck`
  v0.3.1 and shipped in the wheel. No network access at runtime.
- Pure Python, no dependencies, no native code. Tested on Python 3.10 through 3.15.
- The compiled matcher is prebuilt at packaging time and deserialized at import, so the first call
  costs ~20 ms instead of the ~1.1 s a cold compile would take. A version guard falls back to
  compiling from the vendored patterns if the artifact is missing or stale.
- Verified to full parity — matched IDs and coverage percentages — against all 672 of
  `google/licensecheck`'s testdata fixtures.
