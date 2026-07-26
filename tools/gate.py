"""Scan every SPDX licence's own canonical text and check the corpus reports the right thing.

This is the check that has to pass before a corpus change ships, and it answers both halves of
"did adding a licence break anything?" in one pass over the same 695 texts:

* **Self-match.** A pattern that cannot identify the canonical text of its own licence is
  broken. Nine of the patterns inherited from licensecheck are in exactly that state -- SPDX
  revised the licence text after v3.10 and the pattern was never updated -- and the only symptom
  is that a real file carrying today's wording comes back unidentified.
* **Cross-match.** A pattern that claims *another* licence's canonical text is worse than a
  missing one, because it produces a confident wrong answer. This is the failure mode of adding
  a batch of machine-converted patterns at once: many SPDX licences are near-identical variants
  of MIT, BSD or HPND, and a slightly-too-loose conversion of one will swallow the others.

The expectation for each text is "the licence it is the text of, and nothing else", and the
exceptions live in data/expected-ids.tsv with a reason attached. That file is the regression
baseline: any corpus change that alters what a canonical text classifies as has to change a line
there too, which turns "the results moved" from something invisible into a reviewable diff.

What this cannot see: SPDX's canonical texts fill their variable slots with placeholders like
`<year>` and `<copyright holders>`, which the word splitter discards as HTML-looking tags, so a
wildcard that is too small for a real name or a real year list still passes here. Coverage of
real-world wording is what the hand-corrected patterns and tests/data/licenses are for.

    python -m tools.gate            # report, exit 1 on any failure
    python -m tools.gate --verbose  # also list the passing texts
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.corpus import DATA, CorpusError, read_patterns

TEXTS_FILE = DATA / "spdx-texts.json.gz"
EXPECTED_FILE = DATA / "expected-ids.tsv"

# The empty expectation: this text is not expected to be identified as anything.
NOTHING = ()


@dataclass(frozen=True)
class Outcome:
    id: str
    """The licence whose canonical text was scanned."""

    expected: tuple[str, ...]
    actual: tuple[str, ...]
    percent: float

    @property
    def ok(self) -> bool:
        return self.actual == self.expected

    def describe(self) -> str:
        expected = ", ".join(self.expected) or "(nothing)"
        actual = ", ".join(self.actual) or "(nothing)"
        return f"{self.id}: expected {expected}, got {actual} at {self.percent:.1f}% coverage"


def load_texts(path: Path = TEXTS_FILE) -> dict[str, str]:
    """The canonical licence texts of the pinned SPDX release, vendored so the gate runs offline."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_expected(path: Path = EXPECTED_FILE) -> dict[str, tuple[str, ...]]:
    """Parse expected-ids.tsv: `<licence>\\t<ids or '-'>\\t<reason>`."""
    expected: dict[str, tuple[str, ...]] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2].strip():
            raise CorpusError(f"{path.name}:{lineno}: expected '<licence>\\t<ids>\\t<reason>', got {line!r}")
        lid, ids = parts[0].strip(), parts[1].strip()
        if lid in expected:
            raise CorpusError(f"{path.name}:{lineno}: duplicate entry for {lid!r}")
        expected[lid] = NOTHING if ids == "-" else tuple(sorted({i.strip() for i in ids.split(",")}))
    return expected


def run(scanner, texts: dict[str, str], expected: dict[str, tuple[str, ...]], threshold: float) -> list[Outcome]:
    """Scan every text and pair the result with what was expected of it."""
    corpus_ids = set(scanner.ids)
    outcomes = []
    for lid, text in texts.items():
        coverage = scanner.scan(text)
        # Mirrors identify_license: below the threshold the library reports nothing at all, so
        # that is what the gate has to compare against.
        found = () if coverage.percent < threshold else tuple(sorted({m.id for m in coverage.match}))
        want = expected.get(lid, (lid,) if lid in corpus_ids else NOTHING)
        outcomes.append(Outcome(lid, want, found, coverage.percent))
    return outcomes


def stale_expectations(outcomes: list[Outcome], expected: dict[str, tuple[str, ...]]) -> list[str]:
    """Entries in expected-ids.tsv for licences that are not in the text corpus at all.

    Usually an ID that SPDX has deprecated since the exception was written, which means the
    reason attached to it no longer describes anything.
    """
    return sorted(set(expected) - {o.id for o in outcomes})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true", help="list every text, not just the failures")
    args = parser.parse_args(argv)

    from licenseclassifier import COVERAGE_THRESHOLD
    from licenseclassifier._engine.scan import build_from_source

    texts = load_texts()
    expected = load_expected()
    outcomes = run(build_from_source(), texts, expected, COVERAGE_THRESHOLD)
    failures = [o for o in outcomes if not o.ok]
    patterns = read_patterns()

    if args.verbose:
        for outcome in outcomes:
            print(f"{'ok  ' if outcome.ok else 'FAIL'} {outcome.describe()}")
    else:
        for outcome in failures:
            print(f"FAIL {outcome.describe()}")

    stale = stale_expectations(outcomes, expected)
    for lid in stale:
        print(f"WARN {EXPECTED_FILE.name} has an entry for {lid}, which is not in {TEXTS_FILE.name}")

    exceptions = sum(1 for o in outcomes if o.id in expected)
    print(
        f"\n{len(patterns)} patterns, {len(texts)} canonical texts scanned: "
        f"{len(outcomes) - len(failures)} as expected ({exceptions} of them by an explicit "
        f"exception), {len(failures)} unexpected"
    )
    return 1 if failures or stale else 0


if __name__ == "__main__":
    sys.exit(main())
