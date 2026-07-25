# Releasing licenseclassifier

Releases are published to PyPI by `.github/workflows/release.yml`, triggered by pushing a `v*` tag.
No API token exists anywhere in the repository: PyPI authenticates the workflow itself over OIDC
(a "Trusted Publisher").

## Versioning

CalVer, **`YYYY.MM.MICRO`**, where `MICRO` counts releases within a month starting at `0`:

| Version    | Meaning                       |
| ---------- | ----------------------------- |
| `2026.7.0` | first release of July 2026    |
| `2026.7.1` | second release of July 2026   |
| `2026.8.0` | first release of August 2026  |

`licenseclassifier.__version__` in `src/licenseclassifier/__init__.py` is the **only** place the
version is written down. `pyproject.toml` reads it from there via `[tool.setuptools.dynamic]`, so
there is nothing else to keep in step.

> **Never zero-pad the month.** PEP 440 normalises `2026.07.0` to `2026.7.0`, so a padded version
> renames itself during the build and the published filenames stop matching your git tag. CI rejects
> this, but it is easier not to write it. `tests/test_version.py` also fails on it.

The compatibility policy that the version number cannot express is written down in
[CHANGELOG.md](CHANGELOG.md#versioning). In short: breaking changes to the three public names are
flagged **BREAKING** in the changelog, removals get at least two months of `DeprecationWarning`
first, `_engine/` is private, and a change in which licenses a given text matches is a normal
release rather than a breaking one.

## One-time setup on PyPI

Done once per project, by someone with owner rights on the PyPI project. Nothing here lives in the
repository.

1. Sign in to PyPI → your project (or **Publishing** → **Add a pending publisher** if the project
   does not exist yet) → **Publishing** → **Add a new publisher** → **GitHub**.
2. Enter exactly:

   | Field             | Value                              |
   | ----------------- | ---------------------------------- |
   | Owner             | `Fencer-Security`                  |
   | Repository name   | `licenseclassifier`                |
   | Workflow name     | `release.yml`                      |
   | Environment name  | `pypi`                             |

   The workflow name and environment name must match `.github/workflows/release.yml` — the filename
   itself, and the `environment:` key on its `publish` job. `tests/test_release_docs.py` fails if
   this table and the workflow ever disagree.
3. In GitHub → **Settings** → **Environments**, create an environment named `pypi`. Optionally add
   yourself as a required reviewer; the release then pauses for approval after the tests pass and
   before anything is uploaded.

## Cutting a release

1. **Make sure `main` is green.** The release workflow re-runs the suite, but finding out on a tag is
   worse than finding out on a pull request.

2. **Bump the version.** Edit `src/licenseclassifier/__init__.py`:

   ```python
   __version__ = "2026.8.0"
   ```

3. **Write the changelog entry.** In [CHANGELOG.md](CHANGELOG.md), rename the `## [Unreleased]`
   section to `## [2026.8.0] - 2026-08-14` and open a fresh `## [Unreleased]` above it. A release
   whose entry was never written is the usual way this gets forgotten, so
   `tests/test_version.py::test_changelog_has_a_section_for_the_current_version` fails without it.

4. **Verify locally.** This runs the whole matrix and the packaging checks:

   ```bash
   uvx nox            # tests on 3.10-3.15 + combined coverage, must be 100%
   uvx nox -s lint
   uvx nox -s build   # build + twine check --strict
   ```

5. **Commit and tag.** The tag is the version with a `v` prefix and nothing else:

   ```bash
   git commit -am "Release 2026.8.0"
   git push origin main
   git tag v2026.8.0
   git push origin v2026.8.0
   ```

6. **Watch the workflow.** `gh run watch` or the Actions tab. If you configured a required reviewer
   on the `pypi` environment, approve it when it pauses.

## What CI verifies before it publishes

The `publish` job depends on both jobs below, so a failure in either means nothing is uploaded.

**`build`**

- `python -m build` produces an sdist and a wheel.
- `twine check --strict` passes on both.
- The wheel actually contains `scanner.bin.gz`, `licenses.json.gz` and `py.typed`. Without the first
  of these the wheel still works but silently recompiles the matcher from source on every cold
  start, roughly twenty times slower — a failure with no error message, so it is checked explicitly.
- `dist/` holds exactly one wheel and one sdist, and they agree on the version.
- The version is well-formed CalVer with a real, unpadded month.
- The tag matches the version parsed out of the **built filename**, not the source string, because
  PEP 440 can rewrite a version during the build.

**`smoke-test`** (once per supported interpreter, 3.10 through 3.15)

- Installs the built wheel and runs the full test suite against it.
- Checks out `tests/` **without** `src/`, so `import licenseclassifier` can only resolve to the
  installed wheel, and asserts the resolved path is in `site-packages`. Testing the working tree
  that happens to sit next to the wheel would prove nothing about the wheel.

## When something goes wrong

**The tag did not match the version.** The error names the tag it wanted. Delete and re-tag; nothing
was published.

```bash
git tag -d v2026.8.0 && git push --delete origin v2026.8.0
```

**The publish step failed but the build was fine** (PyPI outage, missing approval). Re-run the failed
jobs from the Actions tab, or trigger the workflow manually via **Run workflow**
(`workflow_dispatch`) — it rebuilds from the current commit and the version guard still applies.

**A release is already on PyPI and is wrong.** PyPI does not allow re-uploading a version, even
after deletion. Yank the bad release (PyPI → **Manage** → **Yank**, which hides it from resolvers
without breaking anyone who pinned it) and cut the next `MICRO`.

**A supported Python version was added.** It must be added in four places at once — `SUPPORTED` in
`noxfile.py`, the CI matrix, the release workflow's smoke-test matrix, and the classifiers in
`pyproject.toml`. `tests/test_supported_versions.py` fails if they disagree.

## Regenerating the vendored scanner

Most releases exist to ship refreshed license data. After changing `_engine/licenses.json.gz` or
anything in the matcher, rebuild the prebuilt artifact and commit it:

```bash
uv run python -m licenseclassifier._engine._build
```

`tests/test_prebuilt_artifact.py` fails if the committed artifact does not match a fresh compile, so
forgetting this is caught on the pull request rather than at release time.
