"""Every licence in the corpus must identify its own canonical text, and only its own.

This is the regression gate for corpus changes. It scans the canonical text of all 708 SPDX
licences the corpus covers and compares each result against data/expected-ids.tsv, so the two
ways a corpus edit goes wrong both fail here:

* a pattern that no longer matches its own licence -- which has no symptom other than real files
  coming back unidentified, and is exactly what happened to nine patterns inherited from
  licensecheck when SPDX reworded the licences after v3.10;
* a pattern that claims a *different* licence's text -- the failure mode of adding machine-
  converted patterns in bulk, since many SPDX licences are near-identical variants of MIT, BSD
  or HPND and a slightly loose conversion of one swallows the others.

Where the corpus deliberately reports something else, expected-ids.tsv says so with a reason.
Changing what a licence text classifies as therefore means changing a line in that file, which
is the point: it makes the effect of a corpus change reviewable instead of leaving it as a number
in a test summary.

Skipped when data/ is absent, so a run against an installed wheel still passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from licenseclassifier import COVERAGE_THRESHOLD

REPO_ROOT = Path(__file__).resolve().parents[1]

corpus = pytest.importorskip("tools.corpus", reason="not running from a source checkout")
gate = pytest.importorskip("tools.gate", reason="not running from a source checkout")
refresh = pytest.importorskip("tools.refresh_spdx", reason="not running from a source checkout")

pytestmark = pytest.mark.skipif(not (REPO_ROOT / "data").is_dir(), reason="not running from a source checkout")


@pytest.fixture(scope="module")
def expected() -> dict[str, tuple[str, ...]]:
    return gate.load_expected()


@pytest.fixture(scope="module")
def outcomes(prebuilt_scanner, expected) -> list:
    """One pass over every canonical text, shared by the assertions below.

    Uses the prebuilt artifact rather than a fresh compile because that is what ships, and
    test_prebuilt_artifact.py separately proves it is a faithful build of the same sources.
    """
    return gate.run(prebuilt_scanner, gate.load_texts(), expected, COVERAGE_THRESHOLD)


def test_every_licence_classifies_as_expected(outcomes):
    failures = [o for o in outcomes if not o.ok]
    assert not failures, "\n".join(
        [
            (
                f"{len(failures)} licence text(s) no longer classify as expected. Each is either a "
                "pattern to fix or a deliberate change to record in data/expected-ids.tsv:"
            ),
            *(f"  {o.describe()}" for o in failures),
        ]
    )


def test_no_expectation_is_left_over(outcomes, expected):
    """An entry for a licence that is no longer in the text corpus -- usually one SPDX has
    deprecated -- documents a decision about nothing, and hides that the case is gone."""
    stale = gate.stale_expectations(outcomes, expected)
    assert not stale, f"data/expected-ids.tsv has entries for licences absent from the text corpus: {stale}"


def test_most_licences_need_no_exception(outcomes, expected):
    """The exception list is a budget, not a mechanism to lean on: a corpus where a large share
    of licences report something other than themselves is not identifying licences, it is
    guessing. Raise this deliberately if a batch of exceptions is genuinely justified."""
    assert len(expected) < len(outcomes) // 10


def test_deliberately_excluded_licences_have_no_pattern(outcomes):
    """data/excluded.txt is what stops the refresh tool adding these back. If one acquires a
    pattern anyway, the file is a dead letter and the reasoning in it has been silently lost."""
    excluded = refresh.read_excluded()
    present = sorted(excluded.keys() & {p.id for p in corpus.read_patterns()})
    assert not present, f"data/excluded.txt lists licences that do have a pattern: {present}"


def test_every_pattern_is_gated_or_known_to_be_ungatable(outcomes):
    """A pattern with no canonical text to check is untested by everything in this module.

    Only the non-SPDX IDs licensecheck added should be in that position; anything else means the
    vendored text corpus has drifted from the pattern corpus and the gate is quietly measuring
    less than it appears to.
    """
    texts = gate.load_texts()
    ungated = sorted({p.id for p in corpus.read_patterns()} - set(texts))
    assert ungated == [
        "Aladdin-9",
        "Anti996",
        "BSD-1-Clause-Clear",
        "BSD-3-Clause-NoTrademark",
        "CC-BY-NC-SA-3.0-US",
        "CommonsClause",
        "GPL-2.0-or-3.0",
        "GooglePatentClause",
        "GooglePatentsFile",
        "MIT-NoAd",
        "Prosperity-3.0.0",
    ], "the vendored SPDX texts no longer cover every SPDX licence in the corpus"


def test_coverage_threshold_is_the_libraries_own(outcomes):
    """The gate has to judge by the same rule identify_license applies, or it would pass patterns
    that match a little of their licence and report nothing to a caller."""
    assert all(o.actual == () for o in outcomes if o.percent < COVERAGE_THRESHOLD)
