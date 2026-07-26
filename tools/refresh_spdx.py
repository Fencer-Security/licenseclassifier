"""Bring the corpus up to a given SPDX License List release.

    python -m tools.refresh_spdx                     # latest release, download it
    python -m tools.refresh_spdx --release v3.28.0
    python -m tools.refresh_spdx --from-dir ./spdx    # a local checkout or extracted tarball
    python -m tools.refresh_spdx --summary-file pr.md # markdown summary for a pull request body

Two kinds of staleness accumulate between releases, and this handles both:

**Licences SPDX added.** Each one gets a pattern converted from its matching template
(tools/spdx_lre.py) and appended to data/order.txt, so it sits below every existing pattern in
the matcher's priority order and cannot win a tie against one.

**Licences whose text SPDX revised.** A pattern written against the v3.10 wording of, say,
Apache-1.0 stops matching when SPDX rewords the licence -- silently, since the only symptom is
that a real file comes back unidentified. Those are found by scanning each licence's current
canonical text with the current corpus, and get a *second* pattern under the same ID, generated
from the new template. The hand-corrected pattern is never touched: it still describes the
wording that is in the wild, which is most of what is out there, and the new one covers the rest.

Nothing here decides whether the result is good. It writes files, rebuilds the artifacts, runs
tools/gate.py and reports. A generated pattern that cannot match its own canonical text, or that
claims another licence's, is a gate failure for a human to resolve -- by fixing the pattern, or by
dropping it from data/order.txt, or by recording a deliberate exception in data/expected-ids.tsv.

Existing files are never overwritten, so a hand-corrected pattern survives every refresh.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

from tools import corpus, gate, spdx_lre

RELEASES_URL = "https://api.github.com/repos/spdx/license-list-data/releases/latest"
TARBALL_URL = "https://github.com/spdx/license-list-data/archive/refs/tags/{tag}.tar.gz"
EXCLUDED_FILE = corpus.DATA / "excluded.txt"


class RefreshError(Exception):
    pass


def read_excluded(path: Path = EXCLUDED_FILE) -> dict[str, str]:
    """Licence IDs this project deliberately does not recognise, mapped to why."""
    out: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        lid, _, reason = line.partition("\t")
        if not reason.strip():
            raise RefreshError(f"{path.name}:{lineno}: every exclusion needs a reason after a tab")
        out[lid.strip()] = reason.strip()
    return out


def latest_release() -> str:
    with urllib.request.urlopen(RELEASES_URL, timeout=60) as response:
        return json.load(response)["tag_name"]


def download(tag: str, into: Path) -> Path:
    """Fetch and extract a release's json/ tree, returning the directory holding it."""
    url = TARBALL_URL.format(tag=tag)
    with urllib.request.urlopen(url, timeout=300) as response:
        payload = response.read()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if "/json/" in m.name and m.isfile()]
        if not members:
            raise RefreshError(f"{url} contains no json/ tree")
        root = members[0].name.split("/", 1)[0]
        # Extraction is filtered to regular files under json/, so a crafted archive cannot write
        # outside `into` -- and the members were selected by name above, not taken wholesale.
        tar.extractall(into, members=members, filter="data")
    return into / root


class Release:
    """One SPDX License List release, read from an extracted json/ tree."""

    def __init__(self, directory: Path) -> None:
        listing = directory / "json" / "licenses.json"
        if not listing.is_file():
            raise RefreshError(f"{directory} does not look like license-list-data (no json/licenses.json)")
        payload = json.loads(listing.read_text(encoding="utf-8"))
        self.version: str = payload["licenseListVersion"]
        self.details = directory / "json" / "details"
        self.deprecated = {l["licenseId"] for l in payload["licenses"] if l.get("isDeprecatedLicenseId")}
        self.current = [l["licenseId"] for l in payload["licenses"] if not l.get("isDeprecatedLicenseId")]

    def detail(self, license_id: str) -> dict:
        return json.loads((self.details / f"{license_id}.json").read_text(encoding="utf-8"))

    def text(self, license_id: str) -> str:
        return self.detail(license_id)["licenseText"]

    def template(self, license_id: str) -> str:
        detail = self.detail(license_id)
        template = detail.get("standardLicenseTemplate")
        if not template:
            # Some entries carry only a text; treating it as a template is right, since a text
            # with no variable parts is a template with no directives in it.
            template = detail["licenseText"]
        return template


def write_texts(release: Release, ids: list[str]) -> None:
    """Vendor the canonical texts the gate scans, so the suite needs no network."""
    texts = {lid: release.text(lid) for lid in ids}
    raw = json.dumps(texts, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, mtime=0, fileobj=buf) as gz:
        gz.write(raw)
    gate.TEXTS_FILE.write_bytes(buf.getvalue())


def broken_patterns(release: Release, existing: list[corpus.Pattern], expected: dict) -> list[str]:
    """Corpus licences whose current canonical text the corpus no longer identifies.

    Scanned with a scanner built from the corpus as it stands, before anything is added, so the
    result is about the existing patterns only. Licences with an explicit expectation in
    expected-ids.tsv are skipped: what they report is already a reviewed decision.
    """
    from licenseclassifier import COVERAGE_THRESHOLD
    from licenseclassifier._engine.scan import build_from_source

    scanner = build_from_source()
    known = {p.id for p in existing}
    broken = []
    for lid in release.current:
        if lid not in known or lid in expected:
            continue
        coverage = scanner.scan(release.text(lid))
        found = () if coverage.percent < COVERAGE_THRESHOLD else {m.id for m in coverage.match}
        if lid not in found:
            broken.append(lid)
    return broken


def generate(release: Release, license_id: str, filename: str) -> spdx_lre.Conversion:
    """Convert one licence, writing the pattern only if it can identify its own text.

    A pattern that cannot is worse than no pattern: it is dead weight in the compiled matcher, it
    still contributes false-positive surface through its wildcards, and it makes the corpus claim
    support for a licence it does not recognise. Dropping it leaves the ID unsupported, which is
    the truth, and the next refresh retries it -- so an improvement to the converter or a fix to
    the template upstream picks it up without anyone having to remember.
    """
    conversion = spdx_lre.convert(
        license_id,
        release.template(license_id),
        release.version,
        text=release.text(license_id),
    )
    if conversion.usable:
        (corpus.LICENSES_DIR / filename).write_text(conversion.lre, encoding="utf-8")
    return conversion


def append_to_order(heading: str, lines: list[str]) -> None:
    with corpus.ORDER_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n# {heading}\n")
        f.writelines(f"{line}\n" for line in lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", help="release tag to convert from (default: the latest)")
    parser.add_argument("--from-dir", type=Path, help="a local license-list-data tree, instead of downloading")
    parser.add_argument("--summary-file", type=Path, help="write a markdown summary here")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    if args.from_dir:
        release = Release(args.from_dir)
        tag = args.release or f"v{release.version}"
    else:
        tag = args.release or latest_release()
        directory = corpus.REPO_ROOT / ".spdx-release"
        directory.mkdir(exist_ok=True)
        release = Release(download(tag, directory))

    recorded = json.loads(corpus.SPDX_VERSION_FILE.read_text(encoding="utf-8"))
    excluded = read_excluded()
    expected = gate.load_expected()
    existing = corpus.read_patterns()
    known = {p.id for p in existing}
    on_disk = {name for _, _, name in corpus.read_order()}

    additions = [lid for lid in release.current if lid not in known and lid not in excluded]
    revised = [lid for lid in broken_patterns(release, existing, expected) if f"{lid}.spdx-{tag}.lre" not in on_disk]

    print(f"corpus is at SPDX {recorded['spdx_license_list_version']}, refreshing to {tag} ({release.version})")
    print(f"  {len(additions)} licences to add")
    print(f"  {len(revised)} licences whose current text the corpus no longer matches")
    print(f"  {len(excluded)} deliberately excluded ({EXCLUDED_FILE.name})")
    if args.dry_run:
        for lid in additions:
            print(f"  + {lid}")
        for lid in revised:
            print(f"  ~ {lid}")
        return 0

    notes: dict[str, list[str]] = {}
    unusable: dict[str, float] = {}

    def convert_all(ids: list[str], filename) -> list[str]:
        written = []
        for lid in ids:
            conversion = generate(release, lid, filename(lid))
            if conversion.notes:
                notes[lid] = conversion.notes
            if conversion.usable:
                written.append(lid)
            else:
                unusable[lid] = conversion.coverage or 0.0
        return written

    added = convert_all(additions, lambda lid: f"{lid}.lre")
    if added:
        append_to_order(f"Generated from the SPDX License List {tag} by tools/spdx_lre.py.", added)
    reworded = convert_all(revised, lambda lid: f"{lid}.spdx-{tag}.lre")
    if reworded:
        append_to_order(
            f"SPDX reworded these in {tag}; the pattern above each ID still covers the older "
            f"wording, which is the one in the wild.",
            [f"{lid} pattern={lid}.spdx-{tag}.lre" for lid in reworded],
        )
    for lid, coverage in sorted(unusable.items()):
        print(f"  skipped {lid}: the converted pattern matches only {coverage:.0f}% of its own text")

    # The IDs worth vendoring a text for: everything current, plus anything the corpus still
    # recognises that SPDX has since deprecated -- those patterns need gating too.
    ids = sorted(set(release.current) | (known & release.deprecated))
    write_texts(release, ids)
    corpus.SPDX_VERSION_FILE.write_text(
        json.dumps(
            {
                "spdx_license_list_version": tag,
                "source": f"https://github.com/spdx/license-list-data/tree/{tag}",
                "curated_from": recorded["curated_from"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    corpus.build()
    from licenseclassifier._engine import _build

    _build.main()

    print()
    failures = gate.main([])
    if args.summary_file:
        args.summary_file.write_text(_summary(tag, release, added, reworded, notes, unusable), encoding="utf-8")
    return failures


def _summary(tag: str, release: Release, additions: list[str], revised: list[str], notes: dict, unusable: dict) -> str:
    lines = [
        f"# SPDX License List {tag}",
        "",
        (
            f"Corpus refreshed from [license-list-data {tag}]"
            f"(https://github.com/spdx/license-list-data/tree/{tag}) ({release.version})."
        ),
        "",
        f"- **{len(additions)} licences added**",
        (
            f"- **{len(revised)} licences whose canonical text SPDX has reworded** since the pattern "
            "was written; each keeps its existing pattern and gains a second one for the new wording"
        ),
        "",
        (
            "Patterns are generated from SPDX matching templates and appended below every existing "
            "pattern, so they cannot win a tie against a hand-corrected one. Review the gate output "
            "in the workflow log before merging."
        ),
    ]
    if additions:
        lines += ["", "## Added", "", ", ".join(f"`{lid}`" for lid in additions)]
    if revised:
        lines += ["", "## Reworded upstream", "", ", ".join(f"`{lid}`" for lid in revised)]
    if unusable:
        lines += [
            "",
            "## Skipped: the converted pattern does not match its own canonical text",
            "",
            "These need a hand-written pattern. The next refresh retries them automatically.",
            "",
        ]
        lines += [f"- `{lid}` -- {coverage:.0f}% of its own text" for lid, coverage in sorted(unusable.items())]
    if notes:
        lines += ["", "## Conversions that lost precision", ""]
        lines += [f"- `{lid}`: {'; '.join(items)}" for lid, items in sorted(notes.items())]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
