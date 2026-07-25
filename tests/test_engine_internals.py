"""Corners of the matching engine that no real licence text reaches.

The engine is a line-by-line port of google/licensecheck, so it carries machinery for
pattern shapes the 423 vendored patterns never use (empty groups, an optional wildcard, two
alternatives with the same first two words) and for matcher situations the shipped corpus
happens not to produce (two patterns matching the same span, a word glued to its successor
right where one pattern ends and a longer one continues). Those paths are still live code:
they decide what a regenerated pattern set would match, and a silent behaviour change there
would show up as a mis-identified licence, not as an error.

Reaching them through `identify_license` would mean inventing licence text and hoping the
leading-phrase index routes it the right way, so these tests call `re_parse`,
`leading_phrases` and `_can_match_empty` directly, or build a one-pattern `Scanner` from a
handwritten `lre`.

The local scanner helper is deliberately not named `scanner`: that name belongs to conftest,
where it feeds the `identify` fixture.
"""

from __future__ import annotations

import pytest

from licenseclassifier._engine.dictionary import ANY_WORD, BAD_WORD, Dict
from licenseclassifier._engine.matcher import _can_match_empty
from licenseclassifier._engine.resyntax import (
    OP_ALTERNATE,
    OP_WORDS,
    ReSyntax,
    leading_phrases,
    re_parse,
)
from licenseclassifier._engine.scan import Scanner


def parsed(lre: str) -> tuple[Dict, ReSyntax]:
    """Parse one pattern into a fresh dictionary, so word ids are local to the test."""
    d = Dict()
    return d, re_parse(d, lre)


def scanner_for(*lres: str) -> Scanner:
    """A scanner over synthetic patterns, ided `P0-1.0`, `P1-1.0`, ... in the order given."""
    return Scanner([{"id": f"P{i}-1.0", "lre": lre} for i, lre in enumerate(lres)])


def matches(sc: Scanner, text: str) -> list[tuple[str, int, int]]:
    return [(m.id, m.start, m.end) for m in sc.scan(text).match]


class TestLeadingPhrases:
    """Every scan is anchored on a two-word phrase: `match` only tries the DFA at positions
    whose (previous, current) word pair is in the index. A phrase that goes missing makes the
    pattern unmatchable at that position, and a spurious one only costs time -- so the exact
    phrase set per pattern shape is worth pinning."""

    def test_an_empty_pattern_contributes_only_the_no_phrase_sentinel(self):
        """`(( ))` parses to OP_EMPTY, which matches nothing, so its phrase is the pair of
        BadWord sentinels -- a pair no real word sequence can produce."""
        _d, tree = parsed("(( ))")
        assert leading_phrases(tree) == [(BAD_WORD, BAD_WORD)]

    def test_a_wildcard_may_start_anywhere(self):
        _d, tree = parsed("__3__")
        assert leading_phrases(tree) == [(BAD_WORD, BAD_WORD), (ANY_WORD, BAD_WORD), (ANY_WORD, ANY_WORD)]

    def test_an_optional_wildcard_does_not_repeat_the_no_phrase_sentinel(self):
        """`??` normally has to add the empty phrase, because the pattern may start after the
        optional part. A wildcard already offers it, so the list must come back unchanged
        rather than carrying a duplicate."""
        _d, optional = parsed("__3__??")
        _d2, required = parsed("__3__")
        assert leading_phrases(optional) == leading_phrases(required)

    def test_an_optional_word_adds_the_no_phrase_sentinel(self):
        """The contrast case for the test above: here the sub-expression starts with a real
        word, so the phrase list has to grow one entry for "the optional word is absent"."""
        d, tree = parsed("alpha??")
        assert leading_phrases(tree) == [(d.lookup("alpha"), BAD_WORD), (BAD_WORD, BAD_WORD)]

    def test_alternatives_sharing_their_first_two_words_yield_one_phrase(self):
        d, tree = parsed("(( alpha beta delta || alpha beta gamma ))")
        assert leading_phrases(tree) == [(d.lookup("alpha"), d.lookup("beta"))]

    def test_a_short_alternative_completed_by_the_next_word_is_not_double_counted(self):
        """`alpha` alone is a one-word phrase, so the concatenation completes it with the
        first word that follows the group -- giving (alpha, beta), which the `alpha beta`
        alternative already contributed. The index must still hold a single phrase."""
        d, tree = parsed("(( alpha || alpha beta )) beta gamma")
        assert leading_phrases(tree) == [(d.lookup("alpha"), d.lookup("beta"))]

    def test_a_words_node_with_no_words_has_no_leading_phrase(self):
        """`re_parse` never builds an empty OP_WORDS node, but `leading_phrases` reads
        `w[0]`/`w[1]` positionally, so the empty case has to be answered before those
        indexes are touched."""
        assert leading_phrases(ReSyntax(OP_WORDS)) == [(BAD_WORD, BAD_WORD)]


class TestParsedTreeShape:
    def test_a_nested_group_of_alternatives_is_flattened(self):
        """`((a||b))||c` is a three-way choice, not a choice between a choice and `c`.
        Flattening is what keeps the compiled alternation chain and the leading-phrase walk
        linear in the number of alternatives."""
        d, tree = parsed("(( alpha || beta )) || gamma")
        assert tree.op == OP_ALTERNATE
        assert [sub.op for sub in tree.sub] == [OP_WORDS, OP_WORDS, OP_WORDS]
        assert [sub.w for sub in tree.sub] == [[d.lookup(w)] for w in ("alpha", "beta", "gamma")]


class TestCanMatchEmpty:
    """The compiler asks this to find where the pattern's *required* text ends; trailing parts
    that can match nothing are excluded from the wildcard "cut" optimization. A wrong answer
    silently changes which prefixes the DFA is allowed to abandon."""

    @pytest.mark.parametrize(
        ("lre", "expected"),
        [
            ("alpha?? beta??", True),  # every part of the concatenation is optional
            ("alpha?? beta", False),  # one required word is enough to make it non-empty
            ("alpha beta", False),
            ("(( ))", True),
            ("(( alpha || beta ))??", True),
            ("__3__", True),  # a wildcard may consume nothing
        ],
    )
    def test_a_concatenation_is_empty_matchable_only_if_every_part_is(self, lre, expected):
        _d, tree = parsed(lre)
        assert _can_match_empty(tree) is expected


class TestOverlappingPatterns:
    """Two patterns can match at the same position -- in the shipped corpus that is resolved
    by the pattern text, but the tie-break is positional: the entry listed first in
    licenses.json.gz wins. Asserted in both orders so it pins the rule, not the id."""

    @pytest.mark.parametrize("order", [("P0-1.0", "P1-1.0"), ("P1-1.0", "P0-1.0")])
    def test_the_first_listed_pattern_wins(self, order):
        entries = [{"id": id_, "lre": "alpha beta gamma"} for id_ in order]
        cov = Scanner(entries).scan("alpha beta gamma")
        assert [m.id for m in cov.match] == [order[0]]


class TestGluedWords:
    """The matcher tolerates a missing space: when a word in the text is a pattern word with
    another pattern word stuck to it, both are consumed. If the pattern could have ended at
    the first half, that shorter match has to be recorded before the walk continues into the
    longer pattern -- otherwise a licence whose text has one glued word stops matching at all.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def two_patterns() -> Scanner:
        # P1 is P0 plus two more words, so after "gamma" the DFA state is both a match for P0
        # and mid-pattern for P1.
        return scanner_for("alpha beta gamma", "alpha beta gamma delta epsilon")

    def test_a_glued_word_still_reports_the_shorter_pattern(self, two_patterns):
        """The glued word splits into `gamma` (completing P0) and `delta` (continuing P1). P1
        never completes, so the reported match is P0 -- and its extent stops at the last word
        the text spelled correctly, so the glued word itself does not count as covered."""
        assert matches(two_patterns, "alpha beta gammadelta") == [("P0-1.0", 0, len("alpha beta"))]

    def test_a_glued_word_can_also_complete_the_longer_pattern(self, two_patterns):
        assert matches(two_patterns, "alpha beta gammadelta epsilon") == [
            ("P1-1.0", 0, len("alpha beta gammadelta epsilon"))
        ]
