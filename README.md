# licenseclassifier

Pure-Python SPDX license identification. Give it a blob of license text, get back the SPDX license
IDs it contains, with the character offsets of each match.

```python
from licenseclassifier import identify_license

identify_license(open("LICENSE").read())
# [LicenseIdentificationResult(id='Apache-2.0', start=0, end=11324)]
```

- **No dependencies.** Nothing but the standard library.
- **No native code, no network.** Pure Python, fully offline. The license corpus ships in the wheel.
- **Fast.** ~2 ms to classify a typical license file, after a ~20 ms one-time load.
- **423 SPDX licenses**, plus license-URL recognition.
- **Designed not to guess.** A coverage threshold means it reports nothing rather than something
  wrong.

## Install

```bash
pip install licenseclassifier
```

Python 3.10+.

## Identifying a single license

The common case: you have a `LICENSE` file and you want to know what it is.

```python
from pathlib import Path
from licenseclassifier import identify_license

text = Path("LICENSE").read_text()

for match in identify_license(text):
    print(match.id, match.start, match.end)
```

For a stock Apache 2.0 file that prints:

```
Apache-2.0 0 11324
```

One result, spanning the whole file. Each result is a frozen dataclass:

```
LicenseIdentificationResult(id='Apache-2.0', start=0, end=11324)
```

`start` and `end` are character offsets into the string you passed in, so you can always slice the
matched region back out:

```python
(match,) = identify_license(text)
print(text[match.start : match.end].strip()[:14])
# Apache License
```

If you only care about the identifier, and only expect one:

```python
matches = identify_license(text)
license_id = matches[0].id if matches else None
```

## Identifying multiple licenses

Real projects bundle licenses. A vendored-dependency file, a `THIRD_PARTY_LICENSES`, or a project
that is dual-licensed will contain several license texts one after another. `identify_license`
returns **one result per matched region**, in the order the regions appear:

```python
from pathlib import Path
from licenseclassifier import identify_license

text = Path("COPYING").read_text()  # a 23 KB bundled-licenses file

for match in identify_license(text):
    print(f"{match.id:<14} {match.start:>6} – {match.end}")
```

```
MIT               678 – 1764
NCSA             1845 – 3383
MIT              3628 – 4852
Apache-2.0       4941 – 16298
Zlib            16404 – 17310
Unlicense       17417 – 18627
BSD-2-Clause    18828 – 20214
BSD-3-Clause    20356 – 21868
BSD-2-Clause    21949 – 23251
```

Two things to note.

**Duplicates are real, not a bug.** `MIT` and `BSD-2-Clause` each appear twice because that file
genuinely contains two copies of each — different vendored components under the same license. The
results are regions, not a set. Deduplicate yourself if that's what you want:

```python
distinct = sorted({m.id for m in identify_license(text)})
# ['Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'MIT', 'NCSA', 'Unlicense', 'Zlib']
```

**The offsets let you pull each license out on its own.** The regions are ordered and
non-overlapping, so you get back the individual license texts rather than an unordered bag of IDs —
enough to attribute each one to the component it came from, or to re-emit them separately:

```python
for match in identify_license(text):
    region = text[match.start : match.end]
    print(f"{match.id:<14} {len(region):>6} chars")
```

```
MIT              1086 chars
NCSA             1538 chars
MIT              1224 chars
Apache-2.0      11357 chars
Zlib              906 chars
Unlicense        1210 chars
BSD-2-Clause     1386 chars
BSD-3-Clause     1512 chars
BSD-2-Clause     1302 chars
```

## The coverage threshold

The scanner only reports a license when the matched regions together cover enough of the input.
This is what stops it from claiming your README is MIT-licensed just because it mentions MIT.

```python
identify_license("MIT")
# []

identify_license("This project is released under the MIT license. See LICENSE.")
# []
```

Neither is a license *text*, so neither gets classified. The default threshold is 75% (the same
default licensecheck uses), exposed as `COVERAGE_THRESHOLD`. Override it per call:

```python
from licenseclassifier import identify_license

# Accept files that embed a license alongside a lot of other prose.
identify_license(text, coverage_threshold=40.0)

# Demand a near-verbatim license file and nothing else.
identify_license(text, coverage_threshold=98.0)
```

Lowering the threshold trades precision for recall. The default is deliberately strict: this
library is meant to be trusted, so it prefers returning `[]` over returning a guess.

## Putting it together

Classifying a tree of license files, the way an SBOM or compliance tool would:

```python
from pathlib import Path
from licenseclassifier import identify_license

CANDIDATES = ("LICENSE*", "LICENCE*", "COPYING*", "NOTICE*")

for pattern in CANDIDATES:
    for path in Path("vendor").rglob(pattern):
        if not path.is_file():
            continue
        ids = sorted({m.id for m in identify_license(path.read_text(errors="replace"))})
        print(f"{path}: {', '.join(ids) or 'unidentified'}")
```

## API

The public API is three names, all importable from the top-level package.

### `identify_license(license_text, coverage_threshold=COVERAGE_THRESHOLD)`

Returns `list[LicenseIdentificationResult]` — one entry per matched region, in document order.
Returns `[]` if total coverage falls below `coverage_threshold`.

### `LicenseIdentificationResult`

Frozen dataclass with `id` (SPDX identifier, `str`), `start` and `end` (character offsets, `int`;
`end` is exclusive).

### `COVERAGE_THRESHOLD`

`75.0`. The default minimum percentage of the input that must be recognised license text.

The package ships a `py.typed` marker, so type checkers see the annotations.

`licenseclassifier.__version__` is also available, though it is not part of the three-name contract.

## Versioning

CalVer: **`YYYY.MM.MICRO`**, where `MICRO` counts releases within a month from `0`. So `2026.7.0`
is the first July 2026 release and `2026.7.1` the second. Most of what changes between releases is
the vendored SPDX license data, whose value depends on how recent it is — a date conveys that, a
`MAJOR.MINOR.PATCH` number does not.

Because the number carries no compatibility signal, the guarantees are written down instead:
breaking changes to the three public names are flagged **BREAKING** in
[CHANGELOG.md](CHANGELOG.md), removals are preceded by at least two months of
`DeprecationWarning`, and everything under `_engine/` is private and may change at any time. Note
that identification *results* are not part of the contract: refreshed license data can change which
IDs a given text matches, and that ships as an ordinary release.

If you need to pin, pin an exact version or an upper bound on the year-month — a `~=` or `^`
constraint does not mean anything useful here.

## Performance

Measured on an Apple M-series laptop, classifying an 11 KB Apache 2.0 file:

|                                                | |
| ---------------------------------------------- | ------- |
| `import licenseclassifier`                     | ~7 ms   |
| First call (deserializes the compiled scanner) | ~20 ms  |
| Subsequent calls, median                       | ~2 ms   |

The scanner is built once and cached for the life of the process, so batch workloads pay the
startup cost a single time. The expensive part — compiling ~423 license patterns into a matcher,
about a second of work — is done ahead of time at build time and shipped as a serialized artifact
in the wheel, which is why the first call is 20 ms rather than 1.1 s.

## How it works

Four stages, all ported from `google/licensecheck`:

1. **Tokenization** (`_engine/dictionary.py`) — the text is split into words and canonicalized:
   case folding, accent stripping, `(c)`/`©`/`copyright` normalization, `https`→`http`, and
   skipping HTML and Markdown markup. Words are interned to integer IDs, so everything downstream
   operates on ints rather than strings.
2. **Pattern parsing** (`_engine/resyntax.py`) — the built-in licenses are written in LRE, a small
   regexp-like DSL over words, which is parsed into a syntax tree.
3. **Matching** (`_engine/matcher.py`) — the trees are compiled into a word-level regexp bytecode,
   combined into one program, and run as a Thompson NFA with a lazily built, memoized DFA. Matching
   is leftmost-longest and non-overlapping, and includes context-sensitive spell checking so that
   real-world files with typos still match.
4. **Cover/scan** (`_engine/scan.py`) — turns raw word matches into character offsets, back-fills
   preceding copyright lines into each region, detects license URLs between matches, and computes
   the coverage percentage.

Everything under `_engine/` is private. Treat only the three names above as the supported API.

### Regenerating the compiled scanner

`_engine/scanner.bin.gz` is a build artifact derived from `_engine/licenses.json.gz`. If you change
the license patterns or the matcher, regenerate it:

```bash
python -m licenseclassifier._engine._build
```

A test asserts the committed artifact matches a fresh compile, so CI will tell you if you forgot.
At runtime, a missing, stale or unreadable artifact is not fatal — the scanner falls back to
compiling from `licenses.json.gz`, just more slowly.

## Accuracy

The engine is a faithful port, not an approximation. It was validated to full parity with
`google/licensecheck` v0.3.1 — identical matched license IDs *and* identical coverage percentages —
across all 672 fixtures in that project's testdata.

That parity harness is not currently vendored into this repository, since it needs licensecheck's
Go testdata tree. The in-tree test suite covers the public API, the multi-license case, the
coverage threshold and its boundaries, character-offset correctness on non-ASCII input, and the
integrity of the prebuilt artifact — the last of these on every supported interpreter, because the
artifact is `marshal`-serialized and `marshal` is not guaranteed portable across Python versions.
Vendoring the full parity suite is on the roadmap.

## Prior art and inspiration

This project would not exist without the work below. Credit where it is due:

- **[google/licensecheck](https://github.com/google/licensecheck)** (BSD-3-Clause) — the direct
  ancestor. `licenseclassifier` is a port of its license-identification algorithm, and it vendors
  its LRE license-pattern corpus. If you are working in Go, use licensecheck; this project exists
  so that Python callers don't have to shell out to it or bind to it through cgo. *Not affiliated
  with or endorsed by Google or the Go Authors.*
- **[The SPDX License List](https://spdx.org/licenses/)** (data dedicated to the public domain
  under CC0-1.0) — the underlying source of the license identifiers, and of the matching templates
  licensecheck's patterns were derived from.
- **[google/licenseclassifier](https://github.com/google/licenseclassifier)** (Apache-2.0) — a
  separate Go project that shares this project's name but no code or data. Worth knowing about if
  you got here by searching for the name.
- **[licensee](https://github.com/licensee/licensee)** (MIT) — GitHub's Ruby license detector, the
  thing that puts the license label on a repository page.
- **[askalono](https://github.com/jpeddicord/askalono)** (Apache-2.0) — a Rust detector taking a
  different approach, based on text similarity rather than pattern matching.
- **[scancode-toolkit](https://github.com/aboutcode-org/scancode-toolkit)** (code Apache-2.0, data
  CC-BY-4.0) — the most thorough license and origin scanner in the Python ecosystem, and much
  broader in scope than this library. If you need full provenance scanning rather than "what is
  this license text", use ScanCode.
- **[go-license-detector](https://github.com/go-enry/go-license-detector)** (Apache-2.0) — another
  well-known detector in the Go ecosystem.

## A note on how this was written

**This library was written by a large language model.** The port from Go to Python — the tokenizer,
the LRE parser, the NFA/DFA matcher, the cover layer — was LLM-generated, then verified against the
reference implementation's own test corpus rather than by line-by-line human review.

We think that verification is what makes it trustworthy, and the parity result is the evidence. But
you should know how the code came to be, so you can calibrate accordingly: read it before you
depend on it for anything where a wrong answer is expensive, and please report anything that looks
off.

## Contributing

Issues and pull requests are welcome. The test suite is pytest, and it runs against every
supported interpreter:

```bash
uvx nox                  # the whole matrix: 3.10 through the 3.15 prerelease
uvx nox -s tests-3.10    # one version
uvx nox -- -k artifact   # arguments after -- go to pytest
```

`nox` provides the version matrix — neither pytest nor `uv` has one built in. `uv` provides the
interpreters, so a version you do not have installed is downloaded on first use, prereleases
included; there is nothing to set up by hand.

Each interpreter writes coverage to `.coverage.<version>`, and a final session merges them into one
report — a branch only reachable on one version would otherwise look uncovered. The suite is at
100% line and branch coverage and CI enforces that; the handful of provably unreachable defensive
branches are excluded by name in `[tool.coverage.report]`, with the reasoning recorded there.

For a quick inner loop against your own interpreter:

```bash
uv run pytest
```

The set of supported versions is spelled out in `SUPPORTED` in `noxfile.py`, in the CI matrix, in
the release workflow's smoke-test matrix, and in the Python classifiers in `pyproject.toml`.
`tests/test_supported_versions.py` fails if those ever disagree, so add a version in all four
places at once.

### Releasing

The full runbook is in **[RELEASING.md](RELEASING.md)** — versioning rules, the one-time PyPI Trusted
Publisher setup, what CI verifies before it uploads, and what to do when a release goes wrong.

The short version: bump `__version__` in `src/licenseclassifier/__init__.py` (the only place the
version is written), add the matching section to [CHANGELOG.md](CHANGELOG.md), then tag it.

```bash
git tag v2026.7.0 && git push origin v2026.7.0
```

The library itself has no dependencies; only the test suite does. If you touch anything under
`_engine/`, regenerate the prebuilt scanner (see above) and include the regenerated artifact in
your PR.

## License

`licenseclassifier` is released under the **BSD 3-Clause License**. See [LICENSE](LICENSE).

The license patterns and the algorithm are derived from `google/licensecheck`, which is also
BSD-3-Clause, Copyright (c) 2019 The Go Authors. Its license text is preserved verbatim at
[third_party/licensecheck/LICENSE](third_party/licensecheck/LICENSE), and the derivation is
described in [NOTICE](NOTICE). The whole distribution is therefore uniformly BSD-3-Clause; only the
copyright holders differ between parts.
