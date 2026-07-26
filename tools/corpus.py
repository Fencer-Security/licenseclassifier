"""The reviewable sources of the licence corpus, and the build that produces licenses.json.gz.

``src/licenseclassifier/_engine/licenses.json.gz`` ships in the wheel and is what the runtime
reads, but it is a build artifact: a single 700 KB gzip blob is not something a human can review
in a pull request, and "did this refresh change a licence I care about?" is the only question
that matters when the corpus moves. So the sources live under ``data/`` as one plain-text LRE
pattern per licence, and this module is the only thing that turns them into the artifact:

    data/licenses/<id>.lre   one LRE pattern per licence, filename is the SPDX ID
    data/order.txt           which patterns exist, and in what priority order
    data/urls.tsv            canonical licence URLs, recognised without any matching text
    data/spdx-version.json   which SPDX release the generated patterns were converted from

**Order is behaviour, not presentation.** The matcher reports the lowest-numbered pattern when
two of them match the same span of words (``MultiRE._state_info`` keeps the smallest match
argument), so a pattern's position in ``order.txt`` is its priority. Existing entries therefore
stay put and new ones are appended: an addition can then only change an existing result by
matching *more* words than the incumbent, never by winning a tie against it.

Rebuild after editing anything under ``data/``::

    python -m tools.corpus build                  # rewrites licenses.json.gz
    python -m licenseclassifier._engine._build     # then the compiled scanner

``python -m tools.corpus check`` verifies the committed artifact is in sync without writing;
``tests/test_corpus_sources.py`` runs the same check, so CI fails on a forgotten rebuild.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "data"
LICENSES_DIR = DATA / "licenses"
ORDER_FILE = DATA / "order.txt"
URLS_FILE = DATA / "urls.tsv"
SPDX_VERSION_FILE = DATA / "spdx-version.json"
ARTIFACT = REPO_ROOT / "src" / "licenseclassifier" / "_engine" / "licenses.json.gz"

# licensecheck's License.Type is an enum whose zero value means "not classified". Almost every
# licence leaves it there, so order.txt spells out only the exceptions, as `<id> type=<name>`.
DEFAULT_TYPE = "Unknown"


class CorpusError(Exception):
    """A malformed corpus under data/ -- reported instead of silently building something wrong."""


@dataclass(frozen=True)
class Pattern:
    id: str
    type: str
    lre: str


def read_order() -> list[tuple[str, str, str]]:
    """Parse order.txt into [(id, type, pattern filename)] in priority order.

    A licence may be listed more than once, with an explicit `pattern=` each time. That is how
    a licence whose canonical text SPDX has since revised keeps working: the hand-corrected
    pattern for the wording found in the wild stays exactly as it is, and a second pattern for
    the new wording is appended under the same ID. Both report the same licence, so which one
    wins is not interesting.
    """
    out: list[tuple[str, str, str]] = []
    for lineno, raw in enumerate(ORDER_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        lid, _, rest = line.partition(" ")
        type_ = DEFAULT_TYPE
        pattern = f"{lid}.lre"
        for field in rest.split():
            key, _, value = field.partition("=")
            if key == "type" and value:
                type_ = value
            elif key == "pattern" and value.endswith(".lre"):
                pattern = value
            else:
                raise CorpusError(f"{ORDER_FILE.name}:{lineno}: unknown annotation {field!r}")
        out.append((lid, type_, pattern))
    return out


def read_patterns() -> list[Pattern]:
    """The LRE patterns, in the order given by order.txt.

    Cross-checks the order file against the directory in both directions: a pattern file that
    nothing lists is dead weight that would never be compiled in, and an ID listed without a
    file would drop a licence from the build. Neither has any symptom at runtime beyond a
    licence quietly not being recognised.
    """
    order = read_order()
    listed = [name for _, _, name in order]

    # Checked before the directory, because on a case-insensitive filesystem (macOS, Windows)
    # the second of two colliding files silently never exists and the missing-file error below
    # would fire with a misleading explanation. SPDX IDs are only unique case-sensitively, so
    # this is a real possibility every time upstream adds one.
    seen: set[str] = set()
    folded: dict[str, str] = {}
    for name in listed:
        if name in seen:
            raise CorpusError(f"{ORDER_FILE.name} lists pattern file {name!r} more than once")
        seen.add(name)
        clash = folded.setdefault(name.lower(), name)
        if clash != name:
            raise CorpusError(f"pattern files {clash!r} and {name!r} differ only by case and would collide")

    on_disk = sorted(p.name for p in LICENSES_DIR.glob("*.lre"))
    missing = [name for name in listed if name not in set(on_disk)]
    if missing:
        raise CorpusError(f"{ORDER_FILE.name} lists patterns with no .lre file: {', '.join(missing)}")
    unlisted = [name for name in on_disk if name not in set(listed)]
    if unlisted:
        raise CorpusError(f"data/licenses has .lre files missing from {ORDER_FILE.name}: {', '.join(unlisted)}")

    patterns = []
    for lid, type_, name in order:
        lre = (LICENSES_DIR / name).read_text(encoding="utf-8").strip()
        if not lre:
            raise CorpusError(f"data/licenses/{name} is empty")
        patterns.append(Pattern(lid, type_, lre))
    return patterns


def read_urls() -> list[tuple[str, str]]:
    """Parse urls.tsv into [(url, id)] in file order."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(URLS_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            url, lid = line.split("\t")
        except ValueError:
            raise CorpusError(f"{URLS_FILE.name}:{lineno}: expected '<url>\\t<id>', got {line!r}") from None
        if url != url.lower():
            # Scanner._license_url lowercases before lookup, so a capitalised key never matches.
            raise CorpusError(f"{URLS_FILE.name}:{lineno}: URL must be lowercase: {url!r}")
        if url in seen:
            raise CorpusError(f"{URLS_FILE.name}:{lineno}: duplicate URL {url!r}")
        seen.add(url)
        out.append((url, lid))
    return out


def entries() -> list[dict]:
    """The corpus in the artifact's own shape: patterns first, then the URL-only records.

    Kept in that order because the pattern indices are the priority the matcher applies, and
    URL records carry no pattern and so no priority at all.
    """
    patterns = read_patterns()
    ids = {p.id for p in patterns}
    urls = read_urls()

    # licensecheck asserts the same thing (TestURLs): a URL that resolves to an ID the scanner
    # cannot otherwise report would be the only way to produce that ID, which is a data bug.
    orphans = sorted({lid for _, lid in urls} - ids)
    if orphans:
        raise CorpusError(f"{URLS_FILE.name} maps URLs to IDs with no pattern: {', '.join(orphans)}")

    out = [{"id": p.id, "type": p.type, "lre": p.lre, "url": ""} for p in patterns]
    out += [{"id": lid, "type": DEFAULT_TYPE, "lre": "", "url": url} for url, lid in urls]
    return out


def serialize(data: list[dict]) -> bytes:
    """The artifact bytes. Deterministic: no mtime, no embedded filename, fixed separators, so
    a rebuild that changes no licence produces no diff."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, mtime=0, fileobj=buf) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


def build(path: Path = ARTIFACT) -> list[dict]:
    data = entries()
    path.write_bytes(serialize(data))
    return data


def load_artifact(path: Path = ARTIFACT) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "check"])
    args = parser.parse_args(argv)

    data = entries()
    npat = sum(1 for e in data if e["lre"])
    if args.command == "check":
        if data != load_artifact():
            print("licenses.json.gz is out of date; run `python -m tools.corpus build`", file=sys.stderr)
            return 1
        print(f"licenses.json.gz is in sync ({npat} patterns, {len(data) - npat} URLs)")
        return 0

    ARTIFACT.write_bytes(serialize(data))
    print(f"wrote {ARTIFACT.name} ({npat} patterns, {len(data) - npat} URLs, {ARTIFACT.stat().st_size // 1024} KB)")
    print("now run: python -m licenseclassifier._engine._build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
