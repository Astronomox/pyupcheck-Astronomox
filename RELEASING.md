# Releasing pyupcheck

Releases are published to PyPI by GitHub Actions (`.github/workflows/release.yml`)
using [Trusted Publishing](https://docs.pypi.org/trusted-publishers/). No PyPI
token is stored anywhere: PyPI trusts this repository's release workflow
directly and issues a short-lived upload credential for each run.

## One-time setup

### 1. Tell PyPI to trust the workflow

On pypi.org, open **pyupcheck → Manage → Publishing → Add a new publisher**
(GitHub tab) and enter:

| Field | Value |
|---|---|
| Owner | `Astronomox` |
| Repository name | `pyupcheck-Astronomox` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

### 2. Create the `pypi` environment on GitHub

In the repository: **Settings → Environments → New environment**, name it `pypi`.
Optional but recommended: add yourself under **Required reviewers**, so every
publish waits for your approval.

### 3. Retire the old token

If you have a PyPI API token from the old `upload.bat` flow, delete it at
pypi.org → Account settings → API tokens.

## Cutting a release

```bash
python bump.py minor          # 0.5.0 -> 0.6.0, dates the CHANGELOG
git commit -am "Release 0.6.0"
git tag v0.6.0
git push origin main v0.6.0
```

Pushing the tag runs the workflow, which:

1. Checks the tag matches the version in `pyproject.toml`
2. Runs pyflakes and the test suite
3. Builds the sdist and wheel and validates them with `twine check`
4. Publishes to PyPI (waits for approval if the environment has reviewers)
5. Creates a GitHub Release with the built files and that version's CHANGELOG section

If any step fails, nothing is published. Fix it, delete the tag
(`git tag -d v0.6.0 && git push origin :v0.6.0`), and tag again.
