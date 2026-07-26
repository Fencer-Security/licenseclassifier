"""Measure the time and memory the library costs, for the numbers quoted in the README.

    python -m tools.benchmark
    python -m tools.benchmark --json
    python -m tools.benchmark --artifact /tmp/scanner.bin.gz   # compare another corpus

Memory is the more interesting half. The wheel carries a 601 KB compiled matcher, but that is
gzipped `marshal` output: deserializing it produces about 7 MB of live Python objects and peaks
higher, and the lazily-built DFA then grows on top of that as text is scanned.

That growth is the thing worth knowing about, and it is why the workload stages below are
shaped the way they are. `MultiRE._info` memoizes one entry per distinct DFA state reached, with
no eviction -- which is licensecheck's design, not something added here -- so scanning the *same*
licence a thousand times costs nothing after the first, while scanning a thousand *different*
licences keeps allocating. Batch callers should size for the variety of their input, not its
volume.
"""

from __future__ import annotations

import argparse
import gzip
import importlib
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time

from tools.corpus import DATA, REPO_ROOT

APACHE = REPO_ROOT / "tests" / "data" / "licenses" / "apache-license-2.0"
TEXTS = DATA / "spdx-texts.json.gz"

# Licences a batch tool actually meets over and over, for the repeated-workload stage.
COMMON = ["MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC", "GPL-3.0-or-later", "MPL-2.0"]


def rss_mib() -> float | None:
    """Current resident set size in MiB, or None where it cannot be read."""
    try:
        with pathlib.Path("/proc/self/statm").open() as f:  # Linux
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    except OSError:
        pass
    try:  # macOS and other BSDs
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(out.stdout.strip()) / 1024
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifact", type=pathlib.Path, help="a scanner.bin.gz to measure instead of the shipped one")
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    args = parser.parse_args(argv)

    stages: dict[str, float | None] = {"bare interpreter": rss_mib()}

    t0 = time.perf_counter()
    from licenseclassifier import identify_license

    import_seconds = time.perf_counter() - t0
    scan_module = importlib.import_module("licenseclassifier._engine.scan")
    if args.artifact:
        scan_module._ARTIFACT_PATH = args.artifact
    stages["after import"] = rss_mib()

    # Deserializing the matcher, separately from using it: the two costs are unrelated and only
    # the first one is paid by a caller who imports the library and never scans anything.
    t0 = time.perf_counter()
    scanner = scan_module._load_prebuilt()
    load_seconds = time.perf_counter() - t0
    if scanner is None:
        print(f"{args.artifact or scan_module._ARTIFACT_PATH} did not load", file=sys.stderr)
        return 1
    scan_module._builtin = scanner
    stages["after loading the matcher"] = rss_mib()

    apache = APACHE.read_text(encoding="utf-8")
    with gzip.open(TEXTS, "rt", encoding="utf-8") as f:
        texts: dict[str, str] = json.load(f)

    t0 = time.perf_counter()
    identify_license(apache)
    first_seconds = time.perf_counter() - t0
    stages["after one scan"] = rss_mib()
    states_after_one = len(scanner.re._info)

    timings = []
    for _ in range(200):
        t0 = time.perf_counter()
        identify_license(apache)
        timings.append(time.perf_counter() - t0)
    stages["after 200 scans of the same file"] = rss_mib()

    for i in range(1000):
        identify_license(texts[COMMON[i % len(COMMON)]])
    stages[f"after 1000 files across {len(COMMON)} common licences"] = rss_mib()

    for text in texts.values():
        identify_license(text)
    stages[f"after every one of {len(texts)} distinct licences"] = rss_mib()
    states_after_all = len(scanner.re._info)

    for text in texts.values():
        identify_license(text)
    stages["after a second pass over all of them"] = rss_mib()

    result = {
        "patterns": len(scanner.ids),
        "instructions": len(scanner.re.ops),
        "words": len(scanner.dict.list),
        "dfa_states": {"one_scan": states_after_one, "all_licences": states_after_all},
        "seconds": {
            "import": round(import_seconds, 4),
            "load_matcher": round(load_seconds, 4),
            "first_scan": round(first_seconds, 4),
            "median_scan": round(statistics.median(timings), 5),
        },
        "rss_mib": {k: None if v is None else round(v, 1) for k, v in stages.items()},
    }

    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"{result['patterns']} patterns, {result['instructions']} instructions, {result['words']} words\n")
    # The import figure moves by an order of magnitude depending on whether the .pyc files are in
    # the filesystem cache, so it says more about the machine than about the library.
    print(f"{'import licenseclassifier (cache-sensitive)':<52} {import_seconds * 1000:7.1f} ms")
    print(f"{'deserialize the compiled matcher':<52} {load_seconds * 1000:7.1f} ms")
    print(f"{'first scan, matcher already loaded':<52} {first_seconds * 1000:7.1f} ms")
    print(f"{'median scan thereafter':<52} {statistics.median(timings) * 1000:7.2f} ms\n")
    width = max(len(k) for k in stages)
    for stage, value in stages.items():
        print(f"{stage:<{width}}  {'unavailable' if value is None else f'{value:7.1f} MiB'}")
    print(f"\nDFA states memoized: {states_after_one} after one scan, {states_after_all} after all licences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
