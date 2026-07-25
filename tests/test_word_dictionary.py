"""The canonicalization layer that turns raw license text into comparable words.

This is where a licence file's incidental formatting is normalised away, and it is the part
of the engine most likely to meet input the fixtures do not contain: an HTML-wrapped licence,
a Markdown README, ``(c)`` instead of ``©``, accented names in a copyright line. Everything
here is a pure function of a string, so it is tested directly rather than through a scan.

These assertions describe the port's behaviour as it stands, matched against the intent
documented in dict.go. They are a regression net for the canonicalization rules; the
independent check that the rules agree with ``google/licensecheck`` is the parity harness
against its 672 fixtures, which is not yet vendored here.
"""

from __future__ import annotations

import pytest

from licenseclassifier._engine.dictionary import (
    ANY_WORD,
    BAD_WORD,
    Dict,
    _html_entity_size,
    _html_tag_size,
    _markdown_anchor_size,
    _markdown_link_size,
    fold_rune,
    to_fold,
)

# "Amelie" with a combining acute (NFD) and with a precomposed e-acute (NFC).
NFD_NAME = "Am" + "e\u0301" + "lie"
NFC_NAME = "Am\u00e9lie"


def words_of(text: str) -> list[str]:
    """The words a fresh dictionary interns from ``text``, in order."""
    d = Dict()
    return [d.list[i] for i, _, _ in d.insert_split(text)]


def spans_of(text: str) -> list[tuple[str, int, int]]:
    d = Dict()
    return [(d.list[i], lo, hi) for i, lo, hi in d.insert_split(text)]


class TestFolding:
    @pytest.mark.parametrize(
        ("raw", "folded"),
        [
            ("Á", "a"),
            ("à", "a"),
            ("É", "e"),
            ("ù", "u"),
            ("Ò", "o"),
            ("í", "i"),
        ],
    )
    def test_accented_vowels_fold_to_their_base(self, raw, folded):
        assert fold_rune(raw) == folded

    @pytest.mark.parametrize("dropped", ["̀", "́", "(", ")"])
    def test_combining_accents_and_parentheses_are_dropped(self, dropped):
        assert fold_rune(dropped) is None

    def test_other_characters_are_lowercased(self):
        assert fold_rune("Q") == "q"
        assert fold_rune("7") == "7"

    def test_to_fold_folds_and_drops_across_a_string(self):
        assert to_fold("Ünïcodé (X)") == "ünïcode x"

    def test_to_fold_of_empty_string(self):
        assert to_fold("") == ""

    def test_decomposed_accents_inside_a_word_are_dropped(self):
        """A copyright line copied out of a PDF often arrives in NFD, with the accent as a
        separate combining character. It continues the word rather than ending it, and folds
        away, so the decomposed and precomposed spellings yield the same word."""
        assert words_of(NFD_NAME) == ["amelie"]
        assert words_of(NFD_NAME) == words_of(NFC_NAME)

    def test_decomposed_accents_do_not_extend_the_reported_span(self):
        """The span still covers the combining character, so offsets stay aligned with the
        original text even though the folded word is shorter."""
        assert spans_of(NFD_NAME) == [("amelie", 0, 7)]


class TestCopyrightSpellings:
    """Every way a licence file writes a copyright marker collapses to one word, because
    the patterns are written in terms of that single word."""

    @pytest.mark.parametrize("text", ["Copyright 2026", "COPYRIGHT 2026", "copyright 2026"])
    def test_the_word_itself_is_case_insensitive(self, text):
        assert words_of(text) == ["copyright", "2026"]

    def test_symbol_becomes_the_word(self):
        assert words_of("© 2026") == ["copyright", "2026"]

    def test_parenthesised_c_becomes_the_word(self):
        assert words_of("(c) 2026") == ["copyright", "2026"]

    def test_html_entity_becomes_the_word(self):
        assert words_of("&copy; 2026") == ["copyright", "2026"]

    def test_symbol_next_to_the_word_collapses_to_one(self):
        """ "Copyright ©" is one marker, not two, or it would shift every following offset."""
        assert words_of("Copyright © 2026") == ["copyright", "2026"]
        assert words_of("© Copyright 2026") == ["copyright", "2026"]

    def test_parenthesised_c_span_includes_the_parentheses(self):
        (word, lo, hi), *_ = spans_of("(c) 2026")
        assert (word, lo, hi) == ("copyright", 0, 3)

    def test_bare_c_is_not_a_copyright_marker(self):
        assert words_of("section c applies") == ["section", "c", "applies"]

    def test_entity_that_is_not_copy_is_skipped_entirely(self):
        assert words_of("a &amp; b") == ["a", "b"]


class TestCanonicalRewrites:
    """Equivalences the patterns rely on, so "these terms" matches "the terms"."""

    @pytest.mark.parametrize(
        ("raw", "canonical"),
        [
            ("are", "is"),
            ("them", "it"),
            ("they", "it"),
            ("these", "the"),
            ("this", "the"),
            ("those", "the"),
            ("copies", "copy"),
        ],
    )
    def test_word_is_rewritten(self, raw, canonical):
        assert words_of(f"x {raw} y") == ["x", canonical, "y"]

    def test_https_is_rewritten_to_http(self):
        assert words_of("https://example.org") == ["http", "example", "org"]

    def test_plural_suffix_is_absorbed_into_the_word(self):
        """ "license(s)" is written in licence text to mean either; it folds to "licenses"."""
        assert words_of("the license(s) granted") == ["the", "licenses", "granted"]


class TestMarkupSkipping:
    def test_html_tags_are_skipped(self):
        assert words_of("<p>hello <b>world</b></p>") == ["hello", "world"]

    def test_html_tag_spanning_a_couple_of_newlines_is_skipped(self):
        assert words_of('<a\nhref="x"\n>text</a>') == ["text"]

    def test_markdown_anchor_is_skipped(self):
        assert words_of("Heading {#anchor-name} text") == ["heading", "text"]

    def test_markdown_link_target_is_skipped_but_the_label_is_kept(self):
        assert words_of("see [the terms](https://example.org/a) here") == ["see", "the", "terms", "here"]

    @pytest.mark.parametrize("scheme", ["http://x.org/a", "https://x.org/a", "mailto:a@x.org", "file:/a", "#frag"])
    def test_recognised_link_schemes_are_skipped(self, scheme):
        assert words_of(f"a [b]({scheme}) c") == ["a", "b", "c"]

    def test_unrecognised_link_scheme_is_left_as_words(self):
        """Only the known schemes are treated as markup; anything else is licence prose."""
        assert "ftp" in words_of("a [b](ftp://x.org/a) c")


class TestMarkupSizeHelpers:
    """The size helpers return 0 to mean 'not markup, treat as text'. Each rejection below is
    a case where something markup-shaped must not be swallowed.

    The offset is always passed explicitly rather than searched for: these helpers are asked
    'is there markup starting exactly here?', and the 'nothing starts here' case cannot be
    expressed if the offset is derived from finding the delimiter.
    """

    @pytest.mark.parametrize(
        ("text", "offset"),
        [
            ("<a", 0),  # too short to be a tag at all
            ("x<b>", 0),  # no '<' at the offset
            ("<1bad>", 0),  # does not start with a letter
            ("<a@b>", 0),  # bare '@' -- an email address, not a tag
            ("<a:/b>", 0),  # bare ':/' -- a URL, not a tag
            ("<a\n\n\nb>", 0),  # more than two newlines
            ("<a<b>", 0),  # nested '<'
            ("<abc", 0),  # unterminated
        ],
    )
    def test_html_tag_rejections(self, text, offset):
        assert _html_tag_size(text, offset) == 0

    @pytest.mark.parametrize(
        ("text", "size"),
        [
            ("<b>", 3),
            ("</b>", 4),
            ("<a href='x'>", 12),
            ("<a b@c>", 7),  # '@' after a space is a plain attribute value
            ("<a b:/c>", 8),  # ':/' after a space likewise
        ],
    )
    def test_html_tag_acceptances(self, text, size):
        assert _html_tag_size(text, 0) == size

    @pytest.mark.parametrize(
        ("text", "size"),
        [
            ("&copy;", 6),
            ("&amp;", 5),
            ("&#169;", 6),
            ("&#xA9;", 6),
            ("&#xa9;", 6),
        ],
    )
    def test_html_entity_acceptances(self, text, size):
        assert _html_entity_size(text, 0) == size

    @pytest.mark.parametrize(
        ("text", "offset"),
        [
            ("&x", 0),  # too short
            ("x&amp;", 0),  # no '&' at the offset
            ("&amp", 0),  # unterminated
            ("&#;", 0),  # numeric with no digits
            ("&#12", 0),  # numeric unterminated
            ("&#x;", 0),  # hex with no digits
            ("&#xAB", 0),  # hex unterminated
            ("&#xZZ;", 0),  # not hex digits
        ],
    )
    def test_html_entity_rejections(self, text, offset):
        assert _html_entity_size(text, offset) == 0

    @pytest.mark.parametrize(("text", "size"), [("{#a}", 4), ("{#anchor-name}", 14)])
    def test_markdown_anchor_acceptances(self, text, size):
        assert _markdown_anchor_size(text, 0) == size

    @pytest.mark.parametrize(
        ("text", "offset"),
        [
            ("{#a", 0),  # too short
            ("x{#a}", 0),  # no '{' at the offset
            ("{ab}", 0),  # no '#' after the brace
            ("{#a b}", 0),  # anchors do not contain spaces
            ("{#a\nb}", 0),  # nor newlines
            ("{#ab", 0),  # unterminated
        ],
    )
    def test_markdown_anchor_rejections(self, text, offset):
        assert _markdown_anchor_size(text, offset) == 0

    @pytest.mark.parametrize(
        ("text", "offset"),
        [
            ("]", 0),  # too short
            ("](", 0),  # no recognised scheme follows
            ("x](y", 0),  # no ']' at the offset
            ("](https://a.org/b c)", 0),  # whitespace inside the target
            ("](https://a.org/b", 0),  # unterminated
        ],
    )
    def test_markdown_link_rejections(self, text, offset):
        assert _markdown_link_size(text, offset) == 0


class TestWordSplitting:
    def test_punctuation_and_whitespace_separate_words(self):
        assert words_of("a, b; c.\n\td") == ["a", "b", "c", "d"]

    def test_digits_and_letters_form_single_words(self):
        assert words_of("Version 2.0a") == ["version", "2", "0a"]

    def test_spans_index_the_original_text(self):
        text = "  Hello, world  "
        assert spans_of(text) == [("hello", 2, 7), ("world", 9, 14)]

    def test_spans_are_character_indices_on_non_ascii_input(self):
        text = "Ünïcodé wörd"
        assert spans_of(text) == [("ünïcode", 0, 7), ("wörd", 8, 12)]

    @pytest.mark.parametrize("text", ["", "   ", "!!!", "\n\n"])
    def test_text_without_words_yields_nothing(self, text):
        assert words_of(text) == []


class TestDictInterning:
    def test_insert_is_idempotent(self):
        d = Dict()
        assert d.insert("mit") == d.insert("mit")
        assert d.list == ["mit"]

    def test_insert_assigns_sequential_indices(self):
        d = Dict()
        assert [d.insert(w) for w in ("a", "b", "c")] == [0, 1, 2]

    def test_lookup_of_an_unknown_word_is_bad_word(self):
        assert Dict().lookup("nope") == BAD_WORD

    def test_words_returns_the_intern_list(self):
        d = Dict()
        d.insert("a")
        assert d.words() is d.list

    def test_split_marks_unknown_words_instead_of_interning_them(self):
        """split() is the scanning path: input words the patterns never mention are BAD_WORD,
        and the dictionary must not grow while scanning."""
        d = Dict()
        d.insert("known")
        result = d.split("known unknown")
        assert [i for i, _, _ in result] == [0, BAD_WORD]
        assert d.list == ["known"], "scanning must not intern new words"

    def test_insert_split_grows_the_dictionary(self):
        d = Dict()
        d.insert_split("known unknown")
        assert d.list == ["known", "unknown"]

    def test_sentinels_are_distinct_and_negative(self):
        assert BAD_WORD < 0 and ANY_WORD < 0
        assert BAD_WORD != ANY_WORD
