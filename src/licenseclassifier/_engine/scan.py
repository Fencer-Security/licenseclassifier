"""Pure-Python port of licensecheck scan.go: Scanner.Scan.

Turns the leftmost-longest word matches into a Coverage result: byte/char offsets,
copyright back-fill, inter-match URL detection, and the coverage percentage."""

from __future__ import annotations

import gzip
import json
import marshal
import re
from array import array
from dataclasses import dataclass
from pathlib import Path

from licenseclassifier._engine.dictionary import Dict
from licenseclassifier._engine.matcher import MultiRE
from licenseclassifier._engine.resyntax import re_parse

# Bump whenever the compiled bytecode format or matcher semantics change, so a stale
# prebuilt artifact is rejected and we fall back to compiling from source.
FORMAT_VERSION = 1

MAX_COPYRIGHT_WORDS = 50
# URL pattern ported verbatim from google/licensecheck. The nested group is '/'-delimited
# (the inner class excludes '/'), so matching is linear rather than catastrophic, and it runs
# only on bounded license text at anchored positions — not a ReDoS vector.
_URL_RE = re.compile(r"(?i)https?://[-a-z0-9_.]+\.(?:org|com)(?:/[-a-z0-9_.#?=]+)+/?")


@dataclass(frozen=True)
class Match:
    id: str
    start: int
    end: int
    is_url: bool = False


@dataclass
class Coverage:
    percent: float
    match: list[Match]


class Scanner:
    def __init__(self, entries: list[dict]) -> None:
        self.dict = Dict()
        self.dict.insert("copyright")
        self.dict.insert("http")
        self.ids: list[str] = []
        lres = []
        self.urls: dict[str, str] = {}
        for e in entries:
            if e.get("url"):
                self.urls[e["url"]] = e["id"]
            if e.get("lre"):
                idx = len(self.ids)
                self.ids.append(e["id"])
                syn = re_parse(self.dict, e["lre"])
                lres.append((idx, syn))
        self.re = MultiRE(lres, self.dict)
        self._copyright = self.dict.lookup("copyright")
        self._http = self.dict.lookup("http")

    @classmethod
    def from_compiled(cls, data: dict) -> Scanner:
        """Rebuild a Scanner from a prebuilt compiled artifact (no parsing/compiling)."""
        self = cls.__new__(cls)
        d = Dict()
        d.list = list(data["words"])
        d._map = {w: i for i, w in enumerate(d.list)}
        self.dict = d
        self.ids = list(data["ids"])
        self.urls = dict(data["urls"])
        ops = array("i")
        ops.frombytes(data["ops"])
        args = array("i")
        args.frombytes(data["args"])
        start = {tuple(p) for p in data["start"]}
        self.re = MultiRE.from_compiled(ops, args, start, tuple(data["start_state"]), d)
        self._copyright = d.lookup("copyright")
        self._http = d.lookup("http")
        return self

    def compiled_data(self) -> dict:
        """Serialize the compiled program for the prebuilt artifact."""
        return {
            "version": FORMAT_VERSION,
            "ops": self.re.ops.tobytes(),
            "args": self.re.args.tobytes(),
            "start": [list(p) for p in self.re.start],
            "start_state": list(self.re._start_state),
            "ids": self.ids,
            "urls": self.urls,
            "words": self.dict.list,
        }

    def _license_url(self, url: str) -> str | None:
        url = url.removeprefix("http://").removeprefix("https://")
        url = url.removesuffix("/").removesuffix("/legalcode")
        url = url.lower()
        if url in self.urls:
            return self.urls[url]
        i = url.rfind("/")
        if i >= 0 and url[:i] in self.urls:
            return self.urls[url[:i]]
        return None

    def scan(self, text: str) -> Coverage:
        words, matches = self.re.match(text)
        nwords = len(words)
        cov_matches: list[Match] = []
        total = 0
        last_end = 0
        copyright = self._copyright
        http = self._http
        for mid, mstart, mend in matches + [(-1, nwords, nwords)]:
            if mstart < nwords and last_end < mstart and copyright >= 0:
                limit = max(mstart - MAX_COPYRIGHT_WORDS, last_end)
                for k in range(limit, mstart):
                    if words[k][0] == copyright:
                        mstart = k
                        break

            # URLs before mstart
            k = last_end
            while k < mstart:
                if words[k][0] == http:
                    lo = words[k][1]
                    mobj = _URL_RE.match(text, lo)
                    if mobj is not None:
                        u1 = mobj.end()
                        if mstart == nwords or u1 <= words[mstart][1]:
                            lid = self._license_url(text[lo:u1])
                            if lid is not None:
                                cov_matches.append(Match(lid, lo, u1, True))
                                start_k = k
                                while k < mstart and words[k][2] <= u1:
                                    k += 1
                                total += k - start_k
                                continue
                k += 1

            if mid < 0:
                break

            start = words[mstart][1]
            if mstart == 0:
                start = 0
            else:
                prev = words[mstart - 1][2]
                idx = text.rfind("\n", prev, start)
                if idx >= 0:
                    start = idx + 1
            end = words[mend - 1][2]
            if mend == nwords:
                end = len(text)
            else:
                nxt = words[mend][1]
                idx = text.find("\n", end, nxt)
                if idx >= 0:
                    end = end + (idx - end) + 1
            cov_matches.append(Match(self.ids[mid], start, end, False))
            total += mend - mstart
            last_end = mend

        percent = 100.0 * total / nwords if nwords else 0.0
        return Coverage(percent, cov_matches)


_DATA_PATH = Path(__file__).with_name("licenses.json.gz")  # source patterns (fallback + regen)
_ARTIFACT_PATH = Path(__file__).with_name("scanner.bin.gz")  # prebuilt compiled program
_builtin: Scanner | None = None


def build_from_source() -> Scanner:
    """Compile the scanner from the raw license patterns (the slow path)."""
    with gzip.open(_DATA_PATH, "rt", encoding="utf-8") as f:
        entries = json.load(f)
    return Scanner(entries)


def _load_prebuilt() -> Scanner | None:
    """Load the prebuilt compiled scanner; return None if absent/stale/unreadable."""
    if not _ARTIFACT_PATH.exists():
        return None
    try:
        with gzip.open(_ARTIFACT_PATH, "rb") as f:
            # scanner.bin.gz is a package-internal, version-controlled build artifact, not
            # untrusted input; marshal handles only basic types (no code execution), and any
            # load error is caught below and falls back to compiling from source.
            data = marshal.load(f)
        if data.get("version") != FORMAT_VERSION:
            return None
        return Scanner.from_compiled(data)
    except (OSError, ValueError, EOFError, KeyError, TypeError):
        return None


def builtin_scanner() -> Scanner:
    global _builtin
    if _builtin is None:
        _builtin = _load_prebuilt() or build_from_source()
    return _builtin


def scan(text: str) -> Coverage:
    return builtin_scanner().scan(text)
