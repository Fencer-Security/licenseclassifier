"""licenses.json.gz must be the build of the sources under data/.

The artifact is a 700 KB gzip blob, so nothing about a corpus change is visible in review
except through the plain-text sources it is built from. That only holds while the two agree:
an artifact rebuilt by hand, or a pattern edited under data/ without a rebuild, and the file
that ships stops being the file that was reviewed. Nothing at runtime would notice -- the
scanner reads the artifact and never looks at data/.

The validation tests below cover the ways a corpus edit can be wrong in a way that has no
runtime symptom either: a licence quietly not compiled in, or a pattern file quietly ignored.

Skipped when data/ is absent, so a run against an installed wheel still passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

corpus = pytest.importorskip("tools.corpus", reason="not running from a source checkout")

pytestmark = pytest.mark.skipif(not (REPO_ROOT / "data").is_dir(), reason="not running from a source checkout")


@pytest.fixture
def sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A throwaway corpus under tmp_path, for exercising the validation.

    Returns a callable taking the order.txt body, the pattern files, and the urls.tsv body, so
    each test spells out only the malformation it is about.
    """

    def make(order: str, patterns: dict[str, str] | None = None, urls: str = "") -> None:
        licenses = tmp_path / "licenses"
        licenses.mkdir(exist_ok=True)
        if patterns is None:
            patterns = {lid.split()[0]: "some words here" for lid in order.split("\n") if lid.strip()}
        for lid, lre in patterns.items():
            (licenses / f"{lid}.lre").write_text(lre, encoding="utf-8")
        (tmp_path / "order.txt").write_text(order, encoding="utf-8")
        (tmp_path / "urls.tsv").write_text(urls, encoding="utf-8")
        monkeypatch.setattr(corpus, "LICENSES_DIR", licenses)
        monkeypatch.setattr(corpus, "ORDER_FILE", tmp_path / "order.txt")
        monkeypatch.setattr(corpus, "URLS_FILE", tmp_path / "urls.tsv")

    return make


def test_committed_artifact_matches_the_sources():
    """Fails when data/ was edited without running `python -m tools.corpus build` -- the same
    check that tool's `check` subcommand performs, so CI catches a forgotten rebuild."""
    assert corpus.entries() == corpus.load_artifact(), (
        "licenses.json.gz is out of date with data/; run `python -m tools.corpus build` "
        "and `python -m licenseclassifier._engine._build`"
    )


def test_build_is_deterministic():
    """Two builds of unchanged sources must produce identical bytes, or every regeneration
    would land in review as a diff of the whole compressed blob."""
    data = corpus.entries()
    assert corpus.serialize(data) == corpus.serialize(data)


def test_patterns_come_first_and_in_order_file_order():
    """The artifact's pattern indices are the matcher's tie-break (it reports the lowest
    index that matches a span), so order.txt is behaviour and this is the assertion that
    pins it. URL records carry no pattern, so they sit after all of them."""
    data = corpus.entries()
    listed = [lid for lid, _, _ in corpus.read_order()]
    assert [e["id"] for e in data[: len(listed)]] == listed
    assert all(e["lre"] for e in data[: len(listed)])
    assert all(e["url"] and not e["lre"] for e in data[len(listed) :])


def test_type_annotations_round_trip():
    """order.txt spells out only non-default types, so the one licence that has one is the
    only evidence the annotation is parsed at all."""
    types = {e["id"]: e["type"] for e in corpus.entries() if e["type"] != corpus.DEFAULT_TYPE}
    assert types == {"WTFPL": "Discouraged"}


def test_recorded_spdx_version_is_a_tag_of_the_upstream_data_repo():
    """The refresh tool compares this against the latest release to decide whether the corpus
    is stale, so it has to stay in the shape SPDX tags actually use."""
    recorded = json.loads(corpus.SPDX_VERSION_FILE.read_text(encoding="utf-8"))
    assert recorded["spdx_license_list_version"].startswith("v")
    assert recorded["source"].endswith(recorded["spdx_license_list_version"])


def test_an_unlisted_pattern_file_is_rejected(sources):
    """Adding data/licenses/Foo.lre and forgetting order.txt would compile nothing in."""
    sources("MIT", patterns={"MIT": "words", "Unlisted": "words"})
    with pytest.raises(corpus.CorpusError, match="missing from order.txt: Unlisted"):
        corpus.read_patterns()


def test_a_listed_id_with_no_pattern_file_is_rejected(sources):
    sources("MIT\nGhost", patterns={"MIT": "words"})
    with pytest.raises(corpus.CorpusError, match="no .lre file: Ghost"):
        corpus.read_patterns()


def test_a_repeated_pattern_file_is_rejected(sources):
    """Two identical lines would compile one pattern twice under two indices. A licence listed
    twice with two different `pattern=` files is the supported case and stays legal."""
    sources("MIT\nISC\nMIT")
    with pytest.raises(corpus.CorpusError, match="more than once"):
        corpus.read_patterns()


def test_pattern_files_differing_only_by_case_are_rejected(sources):
    """Zlib.lre and zlib.lre are the same file on macOS and Windows; the corpus would build
    on the machine that added the second one and be short a licence everywhere else."""
    sources("Zlib\nzlib", patterns={"Zlib": "words", "zlib": "other words"})
    with pytest.raises(corpus.CorpusError, match="differ only by case"):
        corpus.read_patterns()


def test_a_second_pattern_for_one_licence_is_accepted(sources):
    """The mechanism that lets a revised canonical text be supported without touching the
    hand-corrected pattern for the wording already in the wild."""
    sources(
        "MIT\nMIT pattern=MIT.spdx-3.28.0.lre",
        patterns={"MIT": "old wording", "MIT.spdx-3.28.0": "new wording"},
    )
    assert [(p.id, p.lre) for p in corpus.read_patterns()] == [("MIT", "old wording"), ("MIT", "new wording")]


def test_an_empty_pattern_file_is_rejected(sources):
    """Scanner skips entries with no LRE, so an empty file is a licence silently dropped."""
    sources("MIT", patterns={"MIT": "\n  \n"})
    with pytest.raises(corpus.CorpusError, match="MIT.lre is empty"):
        corpus.read_patterns()


def test_an_unknown_order_annotation_is_rejected(sources):
    """Better than silently ignoring what someone meant as a directive."""
    sources("MIT priority=1")
    with pytest.raises(corpus.CorpusError, match="unknown annotation"):
        corpus.read_order()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("spdx.org/licenses/MIT MIT", "expected '<url>"),
        ("SPDX.org/licenses/mit\tMIT", "must be lowercase"),
        ("spdx.org/licenses/mit\tMIT\nspdx.org/licenses/mit\tISC", "duplicate URL"),
    ],
)
def test_malformed_url_lines_are_rejected(sources, line, expected):
    """A URL table is invisible in the results until someone scans a file that is nothing but
    a link, so these have to fail at build time."""
    sources("MIT\nISC", urls=line + "\n")
    with pytest.raises(corpus.CorpusError, match=expected):
        corpus.read_urls()


def test_a_url_for_an_unknown_licence_is_rejected(sources):
    """A URL is a shortcut to an ID the scanner can also reach by matching text. One that
    resolves to an ID with no pattern would be the only route to that ID -- which means a typo
    in this table becomes a licence the library reports and can never explain."""
    sources("MIT", urls="spdx.org/licenses/isc\tISC\n")
    with pytest.raises(corpus.CorpusError, match="URLs to IDs with no pattern: ISC"):
        corpus.entries()
