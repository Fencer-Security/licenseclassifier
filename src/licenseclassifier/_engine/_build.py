"""Generate the prebuilt compiled scanner artifact (scanner.bin.gz).

The expensive work (parsing + compiling the ~423 license patterns into the word
regexp bytecode) is done here, ahead of time, and the result is serialized so the
runtime only has to deserialize it. Re-run whenever the license data
(licenses.json.gz) or the matcher/compiler changes:

    python -m licenseclassifier._engine._build
"""

import gzip
import marshal

from licenseclassifier._engine.scan import _ARTIFACT_PATH, build_from_source


def main() -> None:
    scanner = build_from_source()
    data = scanner.compiled_data()
    with gzip.open(_ARTIFACT_PATH, "wb", compresslevel=9) as f:
        # Writes the compiled matcher to a package-internal, version-controlled artifact
        # (trusted, not attacker-controlled); serializing with marshal is not a risk here.
        marshal.dump(data, f)
    print(f"wrote {_ARTIFACT_PATH.name} ({_ARTIFACT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
