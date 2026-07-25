"""The licence-pattern DSL that the 423 vendored patterns are written in.

`re_parse` compiles the `lre` field of each entry in licenses.json.gz. The operators are
`(( ))` grouping, `||` alternation, `??` optional, `__N__` up to N arbitrary words, and
`//** **//` comments. Nothing in the shipped data is malformed, so the parser's rejection
paths are unreachable from normal use -- but they are the guard rail for regenerating
licenses.json.gz from a newer upstream, where a pattern the parser silently mis-reads would
produce a quietly wrong matcher instead of an error.

Grouping, alternation and comments are checked against small purpose-built scanners, which
keeps the assertions readable. `__N__` and optional *groups* are checked against the real
pattern set instead: both depend on the leading-phrase index, which is built across every
pattern at once, so a single-pattern scanner is not representative of them.

The local fixture is deliberately not called `scanner` -- that name belongs to conftest, where
it feeds the `identify` fixture, and shadowing it here would silently repoint the public API
at a one-pattern scanner.
"""

from __future__ import annotations

import gzip
import json
import re

import pytest

from licenseclassifier._engine.dictionary import Dict
from licenseclassifier._engine.resyntax import re_parse
from licenseclassifier._engine.scan import _DATA_PATH, Scanner


def scanner_for(lre: str, *, id: str = "TEST-1.0") -> Scanner:
    return Scanner([{"id": id, "lre": lre}])


def matched_ids(scanner: Scanner, text: str) -> list[str]:
    return [m.id for m in scanner.scan(text).match]


class TestMalformedPatternsAreRejected:
    @pytest.mark.parametrize("lre", ["))", "a ))", "a||b))", "a || ))"])
    def test_unbalanced_closing_group(self, lre):
        with pytest.raises(ValueError, match="unexpected"):
            re_parse(Dict(), lre)

    @pytest.mark.parametrize("lre", ["((", "((a", "((a||b"])
    def test_unbalanced_opening_group(self, lre):
        with pytest.raises(ValueError, match="missing"):
            re_parse(Dict(), lre)

    @pytest.mark.parametrize("lre", ["??", "?? a", "((??))"])
    def test_optional_with_nothing_to_apply_to(self, lre):
        with pytest.raises(ValueError, match="missing argument"):
            re_parse(Dict(), lre)

    def test_unclosed_comment(self):
        with pytest.raises(ValueError, match=r"without closing"):
            re_parse(Dict(), "alpha //** a note that never ends")


class TestWellFormedPatternsParse:
    @pytest.mark.parametrize(
        "lre",
        [
            "alpha beta",
            "(( alpha || beta ))",
            "alpha ?? beta",
            "alpha __3__ beta",
            "alpha //** note **// beta",
            "(( alpha )) (( beta ))",
            "(( alpha || beta )) ?? gamma",
            "alpha __x__ beta",  # not a wildcard; the underscores are literal text
            "(( ))",  # empty group
            "alpha||",  # empty alternative
            "||alpha",
        ],
    )
    def test_parses_without_error(self, lre):
        assert re_parse(Dict(), lre) is not None


class TestOptionalWords:
    """`??` binds to the single word before it, which is why patterns write
    `((free of charge))??` when a whole phrase is optional."""

    @staticmethod
    @pytest.fixture(scope="class")
    def pattern_scanner() -> Scanner:
        return scanner_for("alpha ?? beta gamma")

    @pytest.mark.parametrize("text", ["alpha beta gamma", "beta gamma"])
    def test_the_optional_word_may_be_present_or_absent(self, pattern_scanner, text):
        assert matched_ids(pattern_scanner, text) == ["TEST-1.0"]

    def test_the_following_words_are_still_required(self, pattern_scanner):
        assert matched_ids(pattern_scanner, "alpha gamma") == []

    def test_an_optional_group_in_a_real_pattern_may_be_omitted(self, identify, license_text):
        """The MIT pattern marks "free of charge" as `((free of charge))??`, so a licence
        that drops the phrase must still identify. Checked against the shipped patterns
        because a group's optionality interacts with the leading-phrase index."""
        text = license_text("multi-license")
        start = text.index("Permission is hereby granted, free of charge")
        mit = text[start : start + 1100]
        assert [r.id for r in identify(mit.replace("free of charge, ", "", 1))] == ["MIT"]


class TestAlternation:
    @staticmethod
    @pytest.fixture(scope="class")
    def pattern_scanner() -> Scanner:
        return scanner_for("(( alpha || beta )) gamma")

    @pytest.mark.parametrize("text", ["alpha gamma", "beta gamma"])
    def test_either_alternative_matches(self, pattern_scanner, text):
        assert matched_ids(pattern_scanner, text) == ["TEST-1.0"]

    def test_a_third_word_does_not_match(self, pattern_scanner):
        assert matched_ids(pattern_scanner, "delta gamma") == []


class TestComments:
    """`//** ... **//` annotates a pattern for a human; it must contribute no words, or every
    comment would become text the licence has to contain."""

    @staticmethod
    @pytest.fixture(scope="class")
    def pattern_scanner() -> Scanner:
        return scanner_for("alpha //** explain why **// beta")

    def test_the_comment_body_is_not_required(self, pattern_scanner):
        assert matched_ids(pattern_scanner, "alpha beta") == ["TEST-1.0"]

    def test_the_comment_body_must_not_appear_in_the_text(self, pattern_scanner):
        assert matched_ids(pattern_scanner, "alpha explain why beta") == []


class TestWildcardAgainstTheRealPatterns:
    """`__N__` is what lets one pattern cover every licence file that differs only in its
    copyright line. Exercised through the shipped patterns rather than a synthetic one."""

    @staticmethod
    @pytest.fixture(scope="class")
    def mit_body(license_text) -> str:
        text = license_text("multi-license")
        start = text.index("Permission is hereby granted, free of charge")
        return text[start : start + 1100]

    @pytest.mark.parametrize(
        "holder",
        [
            "2026 A",
            "2026 Fencer Security",
            "2026 A Very Long Company Name Ltd And Its Many Subsidiaries Worldwide",
        ],
    )
    def test_a_copyright_holder_of_any_length_still_identifies(self, identify, mit_body, holder):
        text = f"MIT License\n\nCopyright (c) {holder}\n\n{mit_body}"
        assert [r.id for r in identify(text)] == ["MIT"]

    def test_the_shipped_patterns_actually_use_the_wildcard(self):
        """Guards the test above: if the MIT pattern stopped using `__N__`, that test would
        pass without exercising the wildcard at all."""
        with gzip.open(_DATA_PATH, "rt", encoding="utf-8") as f:
            entries = json.load(f)
        mit = next(e for e in entries if e["id"] == "MIT" and e.get("lre"))
        assert re.search(r"__\d+__", mit["lre"]), "the MIT pattern no longer uses a wildcard"
