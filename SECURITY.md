# Security Policy

## Supported versions

Security fixes go into the latest release. Upgrade with `python -m pip install -U pyupcheck`.

| Version | Supported |
|---------|-----------|
| latest (0.20.x) | Yes |
| older   | No: upgrade |

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private vulnerability reporting (the repository's **Security** tab, then **Report a vulnerability**), with:

- what the problem is and how to reproduce it
- the pyupcheck version
- the impact you see

You'll get a reply within 72 hours. A confirmed vulnerability gets a patched release, and you're credited in the changelog unless you'd rather not be.

## What pyupcheck trusts, and what it doesn't

pyupcheck reads a lot that it doesn't control. Each source, and how it's handled:

| Source | Who controls it | Protection |
|---|---|---|
| Package archives (wheels, sdists) from PyPI | any package author | Read in memory and parsed with `ast`, **never installed, imported or executed**, never extracted to disk. Size limits per download (300 MB), per file (16 MB) and per archive (768 MB uncompressed), so a "zip bomb" can't exhaust memory. A module too deeply nested to parse is skipped. |
| Package metadata (PyPI JSON) | any package author | GitHub owner, repository, branch and path names are validated before they go into a URL. |
| Changelog links on a PyPI page | any package author | Fetched only over `https` on port 443 from a **public** host: never `localhost`, private, link-local (e.g. the cloud metadata service at 169.254.169.254) or internal names. Checked on every redirect too, at most 5 redirects, at most 32 MB per response, counted after decompression. |
| Release notes (GitHub, docs sites) | project maintainers, or whoever controls those pages | Shown as text only: terminal control sequences (window titles, screen clearing, forged links), bidirectional overrides and, in GitHub Actions, workflow commands are removed. In pull-request comments they can't @-mention people, embed images or HTML, or link anywhere. Feeds with a DTD are refused (no entity-expansion attacks). Parsers stay linear-time on hostile input. |
| The project being checked | you, or the author of a repository you cloned or a pull request | Code is parsed, never run (`setup.py` included). Lockfile entries must look like real package names and versions. A file too deeply nested to parse is skipped, not fatal. `migrate --write` never writes through a symlink or outside the project, keeps each file's permissions and line endings, and replaces files atomically. Code lines shown in reports can't break out of their formatting. |
| `review --base` / the Action's `base` input | the workflow author | Must look like a git revision: never something git would read as an option. |

### Credentials

- `GITHUB_TOKEN` (optional) is sent only to `api.github.com` (for its higher rate limit) and `raw.githubusercontent.com` (only after a public request for the file fails). httpx drops it on any redirect to another host.
- `ANTHROPIC_API_KEY` (only for `pyupcheck explain`) is read by the Anthropic SDK and never logged.
- The local cache (`~/.cache/pyupcheck`, or `PYUPCHECK_CACHE_DIR`) is readable only by you (`0700` folder, `0600` files): with a token it can hold the file listing of a private repository.

### What leaves your machine

- Requests to PyPI (`pypi.org`, `files.pythonhosted.org`), GitHub (`api.github.com`, `github.com`, `raw.githubusercontent.com`) and the documentation sites projects link from PyPI, to read metadata and release notes. Your code is **not** sent.
- Only `pyupcheck explain`, which you run on purpose, sends a few lines around each breaking usage to the Claude API. `--dry-run` shows exactly what would be sent, and sends nothing.

### The GitHub Action

- Inputs reach its scripts through environment variables, never by being pasted into them.
- It updates only a comment its own account wrote: someone else's comment that starts with the same marker is left alone.
- On pull requests from forks, GitHub gives the token read-only access, so no comment is posted and the job summary still has the report.
- The repository's own workflows run with read-only tokens by default and pin every third-party action to a commit.

### The VS Code extension

- In a workspace you haven't trusted, a `pyupcheck.path` set by the workspace's own settings is ignored: opening a repository can't make the extension run a program of its choosing.
- pyupcheck is always run directly with a list of arguments, never through a shell, and only with package names and versions that look like real ones.
- The quick fix shows the diff first, and rewrites files only after you confirm.

## Accepted limits

- **Configuration in the checked project.** `[tool.pyupcheck]` in a pull request's `pyproject.toml` (or a `.pyupcheckignore`) can ignore packages or change `fail-on`, like any linter's configuration. Review changes to them like code.
- **DNS rebinding.** A public host name that resolves to an internal address only after pyupcheck checks it could slip past the address check. The other limits (https only, size limits, nothing executed) still apply.
- **The latest release on PyPI.** pyupcheck tells you what an upgrade changes in your code. It doesn't vet whether a release is malicious: use `pip-audit` or similar for known vulnerabilities, and review new dependencies.
