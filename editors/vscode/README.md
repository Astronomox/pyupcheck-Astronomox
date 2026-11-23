# pyupcheck for VS Code

Shows the lines of your Python code that a dependency upgrade breaks, right
where they are, and fixes what can be fixed mechanically with
`pyupcheck migrate`.

- **Errors** on lines that break when your dependencies move to the newest
  versions your constraints allow (removed functions, renamed arguments,
  moved classes). **Warnings** for what's deprecated.
- **Quick fix** (`Ctrl+.`): *Fix with pyupcheck migrate*. It runs in a
  terminal, so you see the diff and anything left for you to change.
- **Re-checks** when you save a requirements file, `pyproject.toml` or a
  lockfile (uv, Poetry, PDM, Pipenv, pylock, conda-lock, pixi). Set
  `pyupcheck.checkOnSave` to `all` to re-check on every Python save too.
- **Notebooks**: findings in `.ipynb` files appear on the cell they're in.
- The status bar shows how many breaking usages there are; click it to re-check.

## Requirements

pyupcheck itself, on the PATH or set in `pyupcheck.path`:

```bash
pip install pyupcheck
```

Set `GITHUB_TOKEN` in the environment VS Code starts from for higher GitHub
rate limits when reading changelogs.

## Settings

| Setting | Default | |
|---|---|---|
| `pyupcheck.path` | `pyupcheck` | The executable, e.g. a virtual environment's |
| `pyupcheck.deep` | `false` | Diff each version's real API (slower, more accurate) |
| `pyupcheck.checkOnSave` | `dependencies` | `dependencies`, `all` or `off` |
| `pyupcheck.minSeverity` | `deprecated` | `breaking` to show only what breaks |
| `pyupcheck.showWarnings` | `false` | Also show low-confidence mentions |

## Development

```bash
npm test                      # unit tests and an activation test, no VS Code needed
npx @vscode/vsce package      # builds pyupcheck-<version>.vsix
code --install-extension pyupcheck-0.1.0.vsix
```
