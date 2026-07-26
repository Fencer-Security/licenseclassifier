"""Convert an SPDX matching template into an LRE pattern.

The SPDX License List publishes, for each licence, a `standardLicenseTemplate` marking up the
canonical text with the parts that may legitimately vary::

    <<beginOptional>>MIT License<<endOptional>>
    <<var;name="copyright";original="Copyright (c) <year> <holders>";match=".{0,5000}">>
    Permission is hereby granted, ... in the <<var;name="Software";original="Software";
    match="Software|Materials">> ...

LRE -- licensecheck's licence regular expressions -- expresses the same idea over words rather
than characters: `(( x ))??` is optional, `(( a || b ))` is alternation, `__N__` matches up to N
arbitrary words, and `//** x **//` is a comment. Punctuation and case are ignored on both sides,
which is what makes the translation tractable: most of what a template's regexes describe is
punctuation and whitespace variation that LRE does not model because it cannot see it.

The conventions below were read off the corpus licensecheck hand-converted from the v3.10
templates, so generated patterns look like the ones already in data/licenses:

    <<beginOptional>> ... <<endOptional>>   ->  (( ... ))??
    a leading copyright var                 ->  //** Copyright **//   (dropped: Scanner.scan
                                                back-fills copyright lines into the region)
    match=".{0,N}" / ".+" / ".*"            ->  __N__, sized by _wildcard_words
    match="Software|Materials"              ->  (( Software || Materials ))

The alternation case is handled by enumerating the regex's language when it is finite and
small: SPDX uses `?`, `|`, groups and short character classes for spelling and wording variants
("acknowledgement|acknowledgment", "makes?", "(The )?ISC License( \\(ISC[L]?\\))?:?"), and
enumerating covers all of them uniformly. Anything unbounded falls back to a wildcard and is
reported in `Conversion.notes` rather than silently approximated.

A converted pattern is a starting point, not a finished one. It reproduces what SPDX says the
licence looks like; it does not know what the licence looks like in the wild, which is what the
hand-corrected patterns in data/licenses encode. tests/test_license_gates.py is what keeps a bad
conversion out: a pattern that cannot match its own canonical text, or that claims someone
else's, does not ship.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from licenseclassifier._engine.dictionary import ANY_WORD, BAD_WORD, Dict
from licenseclassifier._engine.matcher import MultiRE
from licenseclassifier._engine.resyntax import leading_phrases, re_parse

# Enumerating a regex stops here. Past a couple of dozen spellings the alternation is no longer
# describing a variant, and a wildcard is both smaller and more honest about what is known.
MAX_ALTERNATIVES = 24

# Longest wildcard emitted, in words. Bounds both the false-positive surface of a hole in a
# pattern and the compiled program: OP_WILD costs two instructions per word.
MAX_WILDCARD_WORDS = 50

# Characters per word when sizing a wildcard from a character limit. SPDX's `.{0,20}` is a name
# or a short title; four is deliberately generous, since a wildcard that is too small fails to
# match real texts silently, while one that is too large is caught by the cross-match gate.
CHARS_PER_WORD = 4

# Extra words allowed beyond the longest filling known for a slot, for real-world texts that put
# a little more there than SPDX's example does.
WILDCARD_SLACK = 2

# How much of its own canonical text a pattern has to account for, and how many rounds of
# wildcard-doubling to spend getting there. The threshold is the library's own
# COVERAGE_THRESHOLD: below it identify_license reports nothing, so a pattern that does not clear
# it does not identify its licence at all.
TARGET_COVERAGE = 75.0
WIDEN_ROUNDS = 4

_TOKEN = re.compile(r"<<(.*?)>>", re.DOTALL)
_VAR = re.compile(r'var;\s*name="(?P<name>[^"]*)";\s*original="(?P<original>.*)";\s*match="(?P<match>.*)"\Z', re.DOTALL)
_BEGIN_OPTIONAL = re.compile(r"beginOptional(;.*)?\Z", re.DOTALL)
_END_OPTIONAL = re.compile(r"endOptional(;.*)?\Z", re.DOTALL)
_CHAR_LIMIT = re.compile(r"\.\{0,(\d+)\}\Z")
# Whitespace classes are finite as far as LRE is concerned: the word splitter ignores runs of
# whitespace entirely, so any of these is exactly "a gap between two words".
_WHITESPACE_CLASS = re.compile(r"\\s[*+]?|\[\\s\\S\]|\[ \\t\]")


class TemplateError(Exception):
    """A template this converter will not guess at."""


@dataclass
class Conversion:
    id: str
    lre: str
    notes: list[str] = field(default_factory=list)
    """Places where the conversion lost information, for the human reviewing the pattern."""

    coverage: float | None = None
    """Percentage of the licence's own canonical text the pattern matches, if it was checked."""

    @property
    def usable(self) -> bool:
        """False for a pattern that cannot identify the licence it was generated from."""
        return self.coverage is None or self.coverage >= TARGET_COVERAGE


def convert(license_id: str, template: str, spdx_version: str, text: str | None = None) -> Conversion:
    """Convert one SPDX `standardLicenseTemplate` into an LRE pattern.

    Pass the licence's canonical `text` to have the result checked against it, and its wildcards
    widened until it matches. SPDX's `match` regexes and `original` examples routinely understate
    their own slots -- PSF-2.0 declares a copyright variable whose example holds sixteen words
    where the published text has twenty-five -- so a pattern sized from the template alone often
    cannot match the licence it was generated from. `Conversion.coverage` reports how much of the
    text the final pattern accounts for.
    """
    notes: list[str] = []
    header = [
        "//**",
        license_id,
        f"https://spdx.org/licenses/{license_id}.json",
        f"generated from the SPDX License List {spdx_version} matching template",
        "by tools/spdx_lre.py -- edit freely, the refresh tool never overwrites an existing file",
        "**//",
        "",
    ]
    out: list[str] = []
    depth = 0
    seen_required_words = False

    for kind, body in _tokenize(template):
        if kind == "text":
            literal = _literal(body)
            if literal.strip():
                out.append(literal)
                if depth == 0:
                    seen_required_words = True
            continue
        if kind == "beginOptional":
            out.append("((")
            depth += 1
            continue
        if kind == "endOptional":
            if depth == 0:
                raise TemplateError("<<endOptional>> without <<beginOptional>>")
            depth -= 1
            # An optional block that held nothing but a dropped var or punctuation leaves an
            # empty group; drop the whole thing rather than emit `(( ))??`.
            if out[-1] == "((":
                out.pop()
            else:
                out.append("))??")
            continue

        name, original, match = body
        if not seen_required_words and name.lower().startswith("copyright"):
            # The copyright line is not part of any pattern: Scanner.scan walks backwards from
            # the start of a match to pull a preceding copyright line into the reported region,
            # so matching it here would only make the pattern brittle.
            out.append("//** Copyright **//")
            continue
        fragment, note = _variable(name, original, match)
        _emit(out, fragment)
        if note:
            notes.append(note)

    if depth:
        raise TemplateError(f"{depth} unclosed <<beginOptional>>")

    body, trimmed = _anchor(_squeeze(out))
    if trimmed:
        notes.append(f"dropped {trimmed} leading element(s) so the pattern starts with two literal words")
    lre = "\n".join(header + body) + "\n"

    if text is None:
        return Conversion(license_id, lre, notes)

    coverage = self_coverage(lre, text)
    for _ in range(WIDEN_ROUNDS):
        if coverage >= TARGET_COVERAGE:
            break
        widened = _widen(lre)
        if widened == lre:
            break
        lre = widened
        coverage = self_coverage(lre, text)
    else:
        notes.append(f"widened wildcards {WIDEN_ROUNDS} times and still only reached {coverage:.0f}% of its own text")
    return Conversion(license_id, lre, notes, coverage)


def self_coverage(lre: str, text: str) -> float:
    """What percentage of `text`'s words this pattern accounts for, on its own.

    The same figure Scanner.scan computes, but with only this one pattern compiled in, so the
    answer is about the pattern rather than about which of 700 patterns won.
    """
    dictionary = Dict()
    dictionary.insert("copyright")
    dictionary.insert("http")
    matcher = MultiRE([(0, re_parse(dictionary, lre))], dictionary)
    words, matches = matcher.match(text)
    if not words:
        return 0.0
    return 100.0 * sum(end - start for _, start, end in matches) / len(words)


def _widen(lre: str) -> str:
    """Double every wildcard, up to the cap."""
    return _WILDCARD.sub(lambda m: f"__{min(MAX_WILDCARD_WORDS, int(m.group(1)) * 2)}__", lre)


def _anchor(lines: list[str]) -> tuple[list[str], int]:
    """Drop leading elements until the pattern begins with two literal words.

    A pattern has to start with a two-word phrase the matcher can look for: `MultiLRE.Match`
    scans for adjacent word pairs from a precomputed set, so one whose first phrase contains a
    wildcard is never even tried. licensecheck rejects those outright ("invalid pattern: begins
    with wildcard phrase"); this port keeps the parser permissive and the pattern simply never
    matches anything, which is a far worse failure to debug.

    Templates run into this whenever a licence opens with a variable -- "This <<var;file>> is
    free software" -- and the fix is to start the pattern at the first stable pair of words
    instead. What is dropped is a handful of leading words, so the reported region begins a
    little later; Scanner.scan's copyright back-fill already handles preceding text.
    """
    dropped = 0
    while lines and not _starts_with_two_words(lines):
        lines = _drop_leading_element(lines)
        dropped += 1
    if not lines:
        raise TemplateError("no two consecutive literal words anywhere in the template")
    return lines, dropped


def _starts_with_two_words(lines: list[str]) -> bool:
    try:
        phrases = leading_phrases(re_parse(Dict(), "\n".join(lines)))
    except ValueError:
        return False
    return bool(phrases) and all(w not in (ANY_WORD, BAD_WORD) for phrase in phrases for w in phrase)


def _drop_leading_element(lines: list[str]) -> list[str]:
    """Remove the first element: a comment, a wildcard, a group, or a line of words."""
    if lines[0].strip() == "((":
        depth = 0
        for i, line in enumerate(lines):
            depth += line.count("((") - line.count("))")
            if depth == 0:
                return lines[i + 1 :]
    return lines[1:]


_WILDCARD_LINE = re.compile(r"__(\d+)__\Z")
_WILDCARD = re.compile(r"__(\d+)__")


def _emit(out: list[str], fragment: str) -> None:
    """Append a fragment, merging a wildcard into the one before it if nothing separates them.

    Two wildcards with no words between them do not add up -- they collapse to the smaller one.
    The compiler emits a "cut" three words past a wildcard to stop the NFA tracking every
    possible length forever (`_Compile.compile`, OP_WILD), and starting a second wildcard flushes
    that cut immediately; `_trim` then drops every instruction inside the first wildcard's range,
    so the state can no longer be anywhere inside it. `__16__ ."  __5__` therefore matches at
    most five words, not twenty-one.

    Templates produce adjacent wildcards constantly -- a variable holder followed by a variable
    list bullet, separated by nothing but `."` -- and the result is a pattern that cannot match
    its own licence. Summing them keeps the hole the size the template asked for.
    """
    match = _WILDCARD_LINE.match(fragment)
    if match is None:
        out.append(fragment)
        return
    index = _last_significant(out)
    if index is not None:
        previous = _WILDCARD_LINE.match(out[index])
        if previous is not None:
            total = min(MAX_WILDCARD_WORDS, int(previous.group(1)) + int(match.group(1)))
            out[index] = f"__{total}__"
            return
    out.append(fragment)


def _last_significant(out: list[str]) -> int | None:
    """Index of the last emitted line that contributes a word, or is a wildcard."""
    for index in range(len(out) - 1, -1, -1):
        line = out[index]
        if _WILDCARD_LINE.match(line):
            return index
        stripped = line.strip()
        if not stripped or stripped.startswith("//**"):
            continue
        if any(c.isalnum() for c in stripped):
            return index
    return None


def _tokenize(template: str):
    """Split a template into ("text", str) and ("beginOptional"|"endOptional"|"var", body) parts."""
    pos = 0
    for m in _TOKEN.finditer(template):
        if m.start() > pos:
            yield "text", template[pos : m.start()]
        body = m.group(1)
        if _BEGIN_OPTIONAL.match(body):
            yield "beginOptional", None
        elif _END_OPTIONAL.match(body):
            yield "endOptional", None
        else:
            var = _VAR.match(body)
            if var is None:
                raise TemplateError(f"unrecognised template directive <<{body[:60]}>>")
            yield "var", (var.group("name"), var.group("original"), var.group("match"))
        pos = m.end()
    if pos < len(template):
        yield "text", template[pos:]


def _literal(text: str) -> str:
    """Neutralise LRE operators in licence prose.

    Real licence text contains `))` ("Section 2(a))") and stray `?`, and the parser would read
    them as operators. Splitting the pair with a space is enough: every character involved is
    punctuation, which the word splitter discards, so the tokens are unchanged.
    """
    text = text.replace("((", "( (").replace("))", ") )")
    text = text.replace("??", "? ?").replace("//**", "/ /**").replace("**//", "**/ /")
    text = re.sub(r"_{2,}", "_ _", text)
    # Collapse the blank-line runs templates inherit from their source formatting.
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def _variable(name: str, original: str, match: str) -> tuple[str, str | None]:
    """Convert one `<<var>>` into an LRE fragment, plus a note when precision was lost."""
    floor = _word_count(original)
    limit = _CHAR_LIMIT.match(match)
    if limit:
        return _wildcard(int(limit.group(1)), floor), None
    if match in (".+", ".*"):
        return _wildcard(None, floor), None

    alternatives = _enumerate(match)
    if alternatives is None:
        return (
            _wildcard(None, floor),
            f"var {name!r}: match={match!r} is not a bounded alternation, used a wildcard",
        )
    words = _dedupe(alternatives)
    if not words:
        return _wildcard(None, floor), f"var {name!r}: match={match!r} matched no words, used a wildcard"
    if len(words) == 1:
        # A regex with exactly one wording is just text -- but it may still be optional, in
        # which case the empty alternative was dropped by _dedupe and `??` carries it.
        fragment = words[0]
        return (f"(( {fragment} ))??" if "" in alternatives else fragment), None
    body = "\n|| ".join(words)
    quest = "??" if "" in alternatives else ""
    return f"((\n{body}\n)){quest}", None


def _wildcard(chars: int | None, floor: int = 0) -> str:
    return f"__{_wildcard_words(chars, floor)}__"


def _wildcard_words(chars: int | None, floor: int = 0) -> int:
    """How many words to allow for a hole of `chars` characters (None meaning unbounded).

    `floor` is the word count of the template's own `original` value, and it is what makes the
    conversion reliable. SPDX's `match` regexes routinely understate their slots -- `.{0,20}` for
    a var whose own example is "David Giffin <david@giffin.org>" -- and a wildcard sized from the
    regex alone is then too small to match the licence's own canonical text. Sizing from the
    example instead guarantees the text SPDX publishes fits, with a little slack for the
    variations that turn up in real files.
    """
    from_chars = 5 if chars is None else -(-chars // CHARS_PER_WORD)
    return min(MAX_WILDCARD_WORDS, max(1, from_chars, floor + WILDCARD_SLACK if floor else 0))


def _word_count(text: str) -> int:
    """How many words the engine's splitter finds in `text`.

    Deliberately the real splitter and not a regex: it discards `<year>`-style placeholders as
    HTML-looking tags, folds `(c)` to one word, and treats punctuation as separators, so nothing
    else agrees with it about what the canonical text contains.
    """
    return len(Dict().split(text))


def _dedupe(alternatives: list[str]) -> list[str]:
    """Distinct non-empty spellings, in first-seen order.

    Two alternatives that differ only in punctuation are the same pattern to LRE, so they are
    collapsed by comparing the words they reduce to -- otherwise `(The )?ISC License( \\(ISC\\))?`
    would emit four branches for two spellings.
    """
    out: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for alt in alternatives:
        words = tuple(re.findall(r"[0-9A-Za-z]+", alt.lower()))
        if not words or words in seen:
            continue
        seen.add(words)
        out.append(_literal(alt).strip())
    return out


def _squeeze(lines: list[str]) -> list[str]:
    """Drop blank lines that would otherwise stack up where a var or comment was removed."""
    out: list[str] = []
    for line in lines:
        if not line.strip() and (not out or not out[-1].strip()):
            continue
        out.append(line.rstrip())
    while out and not out[-1].strip():
        out.pop()
    return out


# --------------------------------------------------------------------------------------------
# Enumerating a finite regex.
#
# SPDX's non-trivial `match` values are spelling and wording variants, and every one of them is
# a finite language: alternation, optional groups, short character classes, escaped literals.
# Enumerating that language and emitting one LRE branch per spelling is both simpler and more
# faithful than translating regex operators into LRE ones -- and it fails cleanly, by returning
# None, on the unbounded constructs (`+`, `*`, `.`) that LRE has no way to express.
# --------------------------------------------------------------------------------------------


def _enumerate(pattern: str) -> list[str] | None:
    """Every string `pattern` can match, or None if that set is unbounded or too large."""
    pattern = _WHITESPACE_CLASS.sub(" ", pattern)
    try:
        parsed, pos = _alternation(pattern, 0)
    except TemplateError:
        return None
    if pos != len(pattern) or parsed is None or len(parsed) > MAX_ALTERNATIVES:
        return None
    return parsed


def _alternation(pattern: str, pos: int) -> tuple[list[str] | None, int]:
    branches: list[str] = []
    branch, pos = _concatenation(pattern, pos)
    if branch is None:
        return None, pos
    branches.extend(branch)
    while pos < len(pattern) and pattern[pos] == "|":
        branch, pos = _concatenation(pattern, pos + 1)
        if branch is None:
            return None, pos
        branches.extend(branch)
        if len(branches) > MAX_ALTERNATIVES:
            return None, pos
    return branches, pos


def _concatenation(pattern: str, pos: int) -> tuple[list[str] | None, int]:
    out = [""]
    while pos < len(pattern) and pattern[pos] not in "|)":
        atom, pos = _atom(pattern, pos)
        if atom is None:
            return None, pos
        if pos < len(pattern) and pattern[pos] == "?":
            atom = [*atom, ""]
            pos += 1
        elif pos < len(pattern) and pattern[pos] in "*+{":
            return None, pos
        out = [prefix + suffix for prefix in out for suffix in atom]
        if len(out) > MAX_ALTERNATIVES:
            return None, pos
    return out, pos


def _atom(pattern: str, pos: int) -> tuple[list[str] | None, int]:
    c = pattern[pos]
    if c == "(":
        # Both plain groups and the non-capturing form; the distinction is invisible here.
        pos += 1 if not pattern.startswith("(?:", pos) else 3
        inner, pos = _alternation(pattern, pos)
        if inner is None or pos >= len(pattern) or pattern[pos] != ")":
            return None, pos
        return inner, pos + 1
    if c == "[":
        end = pattern.find("]", pos)
        if end < 0:
            return None, pos
        body = pattern[pos + 1 : end]
        # Ranges and negation describe too much to enumerate; a literal set is fine.
        if not body or body[0] == "^" or "-" in body or "\\" in body:
            return None, pos
        return list(body), end + 1
    if c == "\\":
        if pos + 1 >= len(pattern):
            return None, pos
        nxt = pattern[pos + 1]
        # An escaped letter is a class (\d, \w, ...); an escaped anything else is a literal.
        return (None, pos) if nxt.isalpha() else ([nxt], pos + 2)
    # An unquantified `.` is a literal full stop as often as not -- SPDX templates are full of
    # unescaped ones ("(Makefile.in)|(file)", "e.g."). Reading it as punctuation is safe either
    # way: the word splitter discards punctuation, so the only difference between the two
    # readings is one character that never becomes part of a word. A quantified `.` is a real
    # wildcard and `_concatenation` rejects it when it sees the `+`, `*` or `{`.
    return [c], pos + 1
