# The license corpus

The reviewable sources for `src/licenseclassifier/_engine/licenses.json.gz`. The library never
reads this directory — it reads the artifact built from it — but every corpus change happens here,
because a 950 KB gzip blob is not something anyone can review.

| | |
| --- | --- |
| `licenses/<id>.lre` | one LRE pattern per license, named for the SPDX ID |
| `order.txt` | which patterns exist, and in what **priority** order |
| `urls.tsv` | canonical license URLs, recognized in text with no matchable license body |
| `excluded.txt` | SPDX IDs this project deliberately does not recognize, and why |
| `expected-ids.tsv` | what each license's own canonical text is expected to classify as |
| `spdx-texts.json.gz` | those canonical texts, vendored so the gate runs offline |
| `spdx-version.json` | which SPDX release the generated patterns came from |

## Rebuilding

```bash
python -m tools.corpus build                 # data/ -> licenses.json.gz
python -m licenseclassifier._engine._build   # licenses.json.gz -> scanner.bin.gz
python -m tools.gate                         # check nothing broke
```

`tests/test_corpus_sources.py` fails if the committed artifact is out of date, so CI catches a
forgotten rebuild. `python -m tools.corpus check` is the same check on its own.

## Order is behaviour

When two patterns match the same span of words the matcher reports the **lowest-numbered** one
(`MultiRE._state_info` keeps the smallest match argument), so a pattern's position in `order.txt`
is its priority. New patterns are appended and existing lines stay put: an addition can then only
change an existing result by matching *more* words than the incumbent, never by winning a tie
against it. That is what makes adding several hundred licenses at once a safe operation — and it
is why the tie-break lives in a file you can read, rather than in the order a glob happened to
return.

A license may be listed twice, with an explicit `pattern=`. That is how a license whose canonical
text SPDX has reworded keeps working: the hand-corrected pattern for the wording found in the wild
is left exactly as it is, and a second pattern for the new wording is appended under the same ID.

## Writing a pattern

LRE is a word-based pattern language. Case, punctuation and whitespace are ignored on both sides;
`(( x ))??` is optional, `(( a || b ))` is alternation, `__N__` matches up to N arbitrary words,
and `//** x **//` is a comment. The full syntax is in
[licensecheck's licenses/README.md](https://github.com/google/licensecheck/blob/main/licenses/README.md).

Three constraints are not obvious and each produces a pattern that silently never matches:

1. **A pattern must begin with two literal words.** `MultiLRE.Match` scans for adjacent word pairs
   from a precomputed set, so a pattern whose first phrase contains a wildcard is never tried at
   all. licensecheck rejects those at load time; this port's parser is permissive, so the pattern
   simply matches nothing. Start at the first stable pair of words instead — `Scanner.scan` already
   back-fills preceding copyright lines into the reported region.
2. **Adjacent wildcards do not add up.** `__16__` followed by `__5__`, with only punctuation
   between them, matches at most five words: the compiler emits a "cut" that discards the first
   wildcard's alternatives as soon as the second begins. Write one `__21__`.
3. **A wildcard has to fit what real files put there.** Too small and the license silently stops
   being recognized — `PSF-2.0` allowed `__6__` for a copyright year list that has since grown to
   nineteen years, and stopped matching Python's own LICENSE file.

## Adding a license

Normally you do not: `python -m tools.refresh_spdx` converts every license SPDX has added since
the pinned release, and the scheduled workflow opens a pull request. Add one by hand when the
license is not in SPDX at all, or when a converted pattern is too loose or too tight:

1. Write `licenses/<id>.lre` and append the ID to `order.txt`.
2. Rebuild, then run `python -m tools.gate`.
3. If the gate reports the new pattern claiming another license's text, the pattern is too loose.
   If it reports the license as unidentified, it is too tight. If it reports a *different* license
   changing, read that carefully — it is the case this whole arrangement exists to surface.
