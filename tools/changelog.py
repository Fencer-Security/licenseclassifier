"""Pull one release's section out of CHANGELOG.md, to use as GitHub release notes.

The release workflow needs release notes, and the choice is between letting GitHub generate them
from commit subjects and reusing the entry that CHANGELOG.md already carries. The changelog wins:
it is written for the person deciding whether to upgrade, and `tests/test_version.py` already
fails a release whose section was never written, so the input is guaranteed to exist by the time
a tag is pushed.

A section runs from its own `## [version]` heading to the next `## ` heading, which is also how
the file is structured for a reader -- `### Added` and friends nest inside and stay with it.

    python -m tools.changelog 2026.8.0        # print the section body
    python -m tools.changelog 2026.8.0 -o notes.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


class ChangelogError(Exception):
    """The requested version has no section, or the section is empty."""


def section(version: str, text: str) -> str:
    """Return the body of `## [version]`'s section, without the heading itself.

    Raises ChangelogError rather than returning "" for a missing version: an empty string would
    reach GitHub as a release with no notes, which looks deliberate and is not.
    """
    heading = re.compile(rf"^## \[{re.escape(version)}\][^\n]*$", re.MULTILINE)
    found = heading.search(text)
    if not found:
        raise ChangelogError(f"CHANGELOG.md has no '## [{version}]' section")

    rest = text[found.end() :]
    # Any `## ` heading ends the section, including the next release and the trailing prose
    # sections. `### ` subheadings are part of the body and must not match.
    following = re.search(r"^## ", rest, re.MULTILINE)
    body = (rest[: following.start()] if following else rest).strip()

    if not body:
        raise ChangelogError(f"the '## [{version}]' section of CHANGELOG.md is empty")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="the version to extract, without a leading v")
    parser.add_argument("-o", "--output", type=Path, help="write to this file instead of stdout")
    args = parser.parse_args(argv)

    try:
        body = section(args.version, CHANGELOG.read_text(encoding="utf-8"))
    except ChangelogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(body + "\n", encoding="utf-8")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
