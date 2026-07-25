"""Pure-Python port of licensecheck internal/match/dict.go.

Word interning + word splitting with the same canonicalization licensecheck uses:
case fold, accent strip, (s)/(c)/copyright handling, https->http, HTML/Markdown
skipping, and the canonicalRewrites equivalences. Operates on Python str with
code-point indices (self-consistent for patterns and inputs alike)."""

import unicodedata

BAD_WORD = -1
ANY_WORD = -2

# Grave/acute vowels folded to their base (matches dict.go foldRune's switch).
_VOWEL_FOLD = {
    "Á": "a",
    "À": "a",
    "É": "e",
    "È": "e",
    "Í": "i",
    "Ì": "i",
    "Ó": "o",
    "Ò": "o",
    "Ú": "u",
    "Ù": "u",
    "á": "a",
    "à": "a",
    "é": "e",
    "è": "e",
    "í": "i",
    "ì": "i",
    "ó": "o",
    "ò": "o",
    "ú": "u",
    "ù": "u",
}

# canonicalRewrites: the word on the right is parsed as if it were the one on the left.
_CANON = {
    "are": "is",
    "them": "it",
    "they": "it",
    "these": "the",
    "this": "the",
    "those": "the",
    "copies": "copy",
}


def fold_rune(r: str) -> str | None:
    """Fold one char; return None to drop it."""
    if r == "̀" or r == "́":  # combining grave/acute
        return None
    v = _VOWEL_FOLD.get(r)
    if v is not None:
        return v
    if r == "(" or r == ")":
        return None
    return r.lower()


def to_fold(s: str) -> str:
    out = []
    for r in s:
        f = fold_rune(r)
        if f is not None:
            out.append(f)
    return "".join(out)


def _is_word_start(r: str) -> bool:
    return r.isalpha() or r.isdigit() or r == "©"


def _is_word_continue(r: str) -> bool:
    return r.isalpha() or r.isdigit() or unicodedata.category(r) == "Mn"


def _html_tag_size(t: str, i: int) -> int:
    n = len(t)
    if n - i < 3 or t[i] != "<":
        return 0
    k = i + 1
    if t[k] == "/":
        k += 1
    c = t[k]
    if not ("A" <= c <= "Z" or "a" <= c <= "z"):
        return 0
    space = False
    nl = 0
    while k < n:
        c = t[k]
        if c == "@":
            if not space:
                return 0
        elif c == ":":
            if not space and k + 1 < n and t[k + 1] == "/":
                return 0
        elif c == "\r" or c == "\n":
            nl += 1
            if nl > 2:
                return 0
        elif c == "<":
            return 0
        elif c == ">":
            return k + 1 - i
        elif c == " ":
            space = True
        k += 1
    return 0


def _html_entity_size(t: str, i: int) -> int:
    n = len(t)
    if n - i < 3 or t[i] != "&":
        return 0
    if t[i + 1] == "#":
        if t[i + 2] == "x":
            k = i + 3
            while k < n and (t[k].isdigit() or "A" <= t[k] <= "F" or "a" <= t[k] <= "f"):
                k += 1
            if k > i + 3 and k < n and t[k] == ";":
                return k + 1 - i
            return 0
        k = i + 2
        while k < n and t[k].isdigit():
            k += 1
        if k > i + 2 and k < n and t[k] == ";":
            return k + 1 - i
        return 0
    k = i + 1
    while k < n and ("A" <= t[k] <= "Z" or "a" <= t[k] <= "z"):
        k += 1
    if k > i + 1 and k < n and t[k] == ";":
        return k + 1 - i
    return 0


def _markdown_anchor_size(t: str, i: int) -> int:
    n = len(t)
    if n - i < 4 or t[i] != "{" or t[i + 1] != "#":
        return 0
    k = i + 2
    while k < n:
        c = t[k]
        if c == "}":
            return k + 1 - i
        if c == " " or c == "\r" or c == "\n":
            return 0
        k += 1
    return 0


_MD_LINK_PREFIXES = ("http://", "https://", "mailto:", "file:", "#")


def _markdown_link_size(t: str, i: int) -> int:
    n = len(t)
    if n - i < 2 or t[i] != "]" or t[i + 1] != "(":
        return 0
    if not any(t.startswith(p, i + 2) for p in _MD_LINK_PREFIXES):
        return 0
    k = i + 2
    while k < n:
        c = t[k]
        if c in (" ", "\t", "\r", "\n"):
            return 0
        if c == ")":
            return k + 1 - i
        k += 1
    return 0


class Dict:
    def __init__(self) -> None:
        self._map: dict[str, int] = {}
        self.list: list[str] = []

    def insert(self, w: str) -> int:
        i = self._map.get(w)
        if i is not None:
            return i
        i = len(self.list)
        self.list.append(w)
        self._map[w] = i
        return i

    def lookup(self, w: str) -> int:
        return self._map.get(w, BAD_WORD)

    def words(self) -> list[str]:
        return self.list

    def insert_split(self, text: str) -> list[tuple[int, int, int]]:
        return self._split(text, True)

    def split(self, text: str) -> list[tuple[int, int, int]]:
        return self._split(text, False)

    def _emit(self, words: list, w: str, lo: int, hi: int, insert: bool) -> None:
        i = self._map.get(w)
        if i is not None:
            if words and words[-1][0] == i and w == "copyright":
                return  # "Copyright ©" collapses to a single copyright
            words.append((i, lo, hi))
            return
        if insert:
            words.append((self.insert(w), lo, hi))
            return
        words.append((BAD_WORD, lo, hi))

    def _split(self, text: str, insert: bool) -> list[tuple[int, int, int]]:
        words: list[tuple[int, int, int]] = []
        n = len(text)
        i = 0
        while i < n:
            c = text[i]
            if c == "<":
                sz = _html_tag_size(text, i)
                if sz > 0:
                    i += sz
                    continue
            elif c == "{":
                sz = _markdown_anchor_size(text, i)
                if sz > 0:
                    i += sz
                    continue
            elif c == "&":
                sz = _html_entity_size(text, i)
                if sz > 0:
                    if text[i : i + sz] == "&copy;":
                        self._emit(words, "copyright", i, i + sz, insert)
                    i += sz
                    continue
            if c == "]" and i + 1 < n and text[i + 1] == "(":
                sz = _markdown_link_size(text, i)
                if sz > 0:
                    i += sz
                    continue

            if not _is_word_start(c):
                i += 1
                continue

            lo = i
            fr = fold_rune(c)
            buf = [fr] if fr is not None else []
            j = i + 1
            if c != "©":
                while j < n and _is_word_continue(text[j]):
                    f = fold_rune(text[j])
                    if f is not None:
                        buf.append(f)
                    j += 1
                if text[j : j + 3] == "(s)":
                    buf.append("s")
                    j += 3
            hi = j
            w = "".join(buf)
            i = j

            if w == "https":
                w = "http"
            elif w == "c" and lo > 0 and text[lo - 1] == "(" and hi < n and text[hi] == ")":
                w = "copyright"
                lo -= 1
                hi += 1
            elif w == "©":
                w = "copyright"

            w = _CANON.get(w, w)
            self._emit(words, w, lo, hi, insert)

        return words
