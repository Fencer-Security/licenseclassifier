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

## [2026.8.0] - 2026-08-17

### Added

- **Refreshed the license corpus from SPDX v3.10 to v3.28.0: 423 licenses to 693.** The vendored
  data had not moved since `google/licensecheck` assembled it in September 2020, so 285 SPDX
  identifiers were missing — including `BUSL-1.1`, `Elastic-2.0`, `Unicode-3.0`, `CDLA-Permissive-2.0`
  and the whole `HPND-*` family. 270 of them now have patterns, converted from the SPDX matching
  templates by `tools/spdx_lre.py`.
- **Nine licenses the library had silently stopped recognizing now work again.** SPDX reworded
  `Apache-1.0`, `BitTorrent-1.0`, `CAL-1.0`, `CC-BY-ND-2.5`, `CECILL-2.1`, `MIT-CMU`, `PSF-2.0`,
  `SGI-B-1.0` and `mpich2` after v3.10, and each pattern quietly stopped matching its own license —
  a `LICENSE` file with today's wording came back unidentified with no error. Each keeps its original
  pattern, for the wording still in the wild, and gains a second one for the current text. `NASA-1.3`
  never matched at all and now does.
- Measured against every SPDX canonical text, this adds 266 licenses that were previously
  unidentified and resolves 10 more to a specific variant instead of its parent (for example
  `BSD-2-Clause-Darwin` rather than `BSD-2-Clause`). **No license that was identified before is
  identified differently or not at all.**
- `data/` now holds the corpus as reviewable sources — one plain-text LRE pattern per license, an
  `order.txt` recording the matcher's tie-break priority, the URL table, and the deliberate
  exclusions and expectations with reasons attached. `licenses.json.gz` is built from it by
  `python -m tools.corpus build`, and a test fails if the two drift apart. Before this, a corpus
  change was a diff of a 700 KB gzip blob.
- A corpus gate (`tools/gate.py`, `tests/test_license_gates.py`) scans the canonical text of all 708
  covered licenses and asserts each is identified as itself and nothing else, with deviations
  recorded in `data/expected-ids.tsv`. This is what makes adding hundreds of near-identical MIT, BSD
  and HPND variants at once a checkable operation rather than a hopeful one.
- A monthly `spdx-refresh` workflow runs `tools/refresh_spdx.py` against the newest SPDX release and
  opens a pull request with the regenerated corpus, the gate output and a summary of what changed.
  Nothing merges or releases automatically.
- Spot-checked against real license files rather than only SPDX's canonical texts, which fill their
  variable slots with `<year>`-style placeholders the tokenizer discards and so never exercise a
  pattern's wildcards. Elasticsearch's `ELASTIC-LICENSE-2.0.txt` goes from unidentified to
  `Elastic-2.0`; ICU's `LICENSE` from unidentified (57.8% coverage) to fourteen regions at 82.3%,
  including `Unicode-3.0`, `ICU`, `NAIST-2003` and `MIT-0`. `BUSL-1.1` needed hand-correcting after
  conversion — the generated pattern required MariaDB's own copyright year and covenants appendix,
  which no real BUSL file reproduces. It now matches the Terms section of Terraform's and Vault's
  licenses, though at ~50% coverage, since half of such a file is the licensor's own Parameters
  block: `identify_license(text, coverage_threshold=50)` reports it, the default threshold does not.

### Changed

- The wheel is larger: `licenses.json.gz` 707 KB → 950 KB, `scanner.bin.gz` 435 KB → 601 KB. The
  first call after import costs ~30 ms rather than ~20 ms, and a cold compile from source (the
  fallback path) 1.4 s rather than 1.1 s. Steady-state scanning is unchanged at ~2 ms.
- Memory, measured with the new `python -m tools.benchmark`: deserializing the matcher costs 18 MiB
  rather than 13 MiB, and a warm scanner classifying common licenses settles at ~82 MiB rather than
  ~76 MiB. The worst case — a single process that has scanned every license in the corpus — rises
  from 333 MiB to 406 MiB, because the memoized DFA grows with the number of patterns as well as
  with the variety of the input. The README now documents this; the short version is that memory
  tracks input diversity, not input volume, and there is currently no public way to clear the cache.
- Fifteen SPDX licenses are covered by neither a pattern nor an exclusion: the conversion of their
  templates could not match their own canonical text, so it was dropped rather than shipped as dead
  weight. `Cronyx`, `DocBook-DTD`, `DocBook-Stylesheet`, `Furuseth`, `fwlw`, `GD`, `HPND-SMC`,
  `magaz`, `MIPS`, `ssh-keyscan`, `SunPro`, `TPDL`, `TTWL`, `ulem`, `Zeeff`. Each refresh retries
  them.
- Eleven SPDX identifiers are excluded on purpose, following licensecheck's reasoning: SPDX
  distinguishes them by something the license text does not state (`GFDL-*-only`/`-or-later`,
  `OFL-*-RFN`/`-no-RFN`, `CAL-1.0-Combined-Work-Exception`). See `data/excluded.txt`.
- `Net-SNMP` and `bzip2-1.0.5` are still reported although SPDX has deprecated both. Deprecation
  removes an identifier from the list; it does not remove the text from the files that carry it.
- Adopted CalVer (`YYYY.MM.MICRO`); the first release is `2026.8.0` rather than `0.1.0`. The version
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
