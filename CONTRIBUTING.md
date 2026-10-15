# Contributing

## Dev setup

Requires Python 3.12 and [`uv`](https://github.com/astral-sh/uv).

```bash
git clone <repo>
cd review-app
uv sync
```

Install pre-commit hooks (runs ruff lint + format on every commit):

```bash
uv run pre-commit install
```

Activate the commit message hook (enforces conventional commit format):

```bash
git config core.hooksPath .githooks
```

## Running the app

```bash
make dev
```

Development mode enables hot reload and logs to the console in addition to the log file. Use
`make run` to start the app without development mode.

## Common tasks

| Command | What it does |
|---------|-------------|
| `make test` | Run the test suite |
| `make coverage` | Tests with line coverage report (backend only) |
| `make lint` | Run Ruff lint and apply automatic fixes |
| `make format` | Auto-format with ruff |
| `make ci` | Lint + format check + tests (mirrors CI) |
| `make run` | Run the app without development mode |
| `make dev` | Run the app with hot reload and console logging |
| `make build` | Build standalone executable via PyInstaller |
| `make changelog` | Preview the changelog for unreleased commits |
| `make bump` | Bump version based on conventional commits since last tag |
| `make release` | Bump version, commit, and tag — ready to push |
| `make docs` | Serve the user documentation locally |
| `make docs-build` | Build the user documentation with strict checks |
| `make docs-screenshots` | Regenerate documentation screenshots |

## Tests

Tests live in `tests/` and use pytest. Coverage includes backend behavior, pure UI
state, NiceGUI interactions, and Node-backed player scripts. These do not replace
real-browser/media validation for playback or layout changes.

Run them:

```bash
uv run pytest tests/
```

Database tests use isolated temporary SQLite databases; do not point tests or
cleanup worktrees at your live application data directory.

When adding backend functionality, add a corresponding test. The existing files are a reasonable guide for where things belong:

| File | Covers |
|------|--------|
| `test_migrations.py` | Migration runner, idempotency |
| `test_project_and_import.py` | Project CRUD, model CSV import |
| `test_queue_and_export.py` | Video queue filtering, annotation export |
| `test_provider_misc.py` | Settings, video sync, reprobe |
| `test_species.py` | Species/behavior loading and matching |

## Database migrations

Migrations live in `review_app/backend/db/migrations.py` as a versioned list. The rules:

- **Never modify or remove a published entry** — migrations are applied once per database and tracked by version number. The private Distance chain was consolidated before release; see the [cleanup plan](docs/decisions/distance-branch-cleanup-plan.md) for its explicit development-database rebuild.
- To make a schema change, append a new `(version, sql)` entry. Versions must be contiguous starting at 1.
- Use a callable (instead of a SQL string) when the migration needs conditional logic or multiple steps — see `_migration_v4` for an example.
- Test both fresh construction and the supported upgrade path. Fresh databases use ORM `create_all()` and are stamped current without executing migration SQL. Keep the published v22 schema fixture as an upgrade regression; do not replace it with a current snapshot just to make parity checks pass.
- Reject newer schema versions rather than decrementing their stamp. The one-off development converter writes a separate output and is not an automatic startup migration.

## Code layout

```
review_app/
  app/                   # UI layer (NiceGUI pages, state, translations)
    pages/
      review/            # Ordinary Review
      distance_review/   # Distance composition, state, dialogs and player scripts
  backend/
    db/
      models.py          # SQLAlchemy schema
      migrations.py      # Frozen upgrade definitions and version guard
      backup.py          # Backup/restore and dedicated errors
    provider/
      local_data_provider.py  # Main data access class
      distance_*.py      # Public facade and session-aware Distance responsibilities
      video.py           # Probing, sync and video queries
      species.py         # Vocabulary and species queries
    distance_timing.py   # Pure camera-clock/sample timing
```

`LocalDataProvider` composes feature mixins as its public API. Distance's internal
helpers use the caller's SQLAlchemy session so sample/infant/completion changes
share one transaction. Keep domain helpers independent of NiceGUI; the existing
provider/config integration remains outside this focused cleanup.

## Linting and formatting

Ruff is configured in `pyproject.toml` (line length 99, isort enabled). The pre-commit hook auto-fixes and formats on commit. To apply the same fixes manually:

```bash
make lint      # lint and apply automatic fixes
make format    # fix in place
```

Pre-commit hooks enforce both on every commit. Run `make ci` for non-modifying checks before
pushing.

## Building a release

The project uses [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`, with pre-releases as `1.0.0-beta.1`, `1.0.0-rc.1`, etc.

Commit messages must follow [conventional commits](https://www.conventionalcommits.org/) — the format is enforced by the commit-msg hook and used to auto-generate changelogs:

```
feat: add species filter
fix: handle missing videos
refactor: extract col_val helper
```

Types that appear in the changelog: `feat`, `fix`, `refactor`, `perf`, `docs`. Types that are valid but hidden: `chore`, `test`, `ci`, `build`.

### Release steps

The next version is inferred automatically from conventional commits since the last tag: any `feat:` → minor bump, `fix:`/others → patch bump, breaking change (`!` or `BREAKING CHANGE:` footer) → major bump.

```bash
# Preview what the next version will be and what's going into the changelog
uvx git-cliff --bumped-version
make changelog

# Bump, commit, and tag in one step
make release

# Push — CI handles the rest (single command ensures the tag push triggers GitHub Actions)
git push origin main v1.0.0
```

To override the version (e.g. for a planned major release):

```bash
make release VERSION=2.0.0
```

GitHub Actions builds executables for Linux, Windows, and macOS, generates a changelog from commits since the last tag, and attaches everything to a GitHub Release. Tags containing `beta`, `alpha`, or `rc` are automatically marked as pre-releases.

To preview the changelog for the upcoming release locally:

```bash
uvx git-cliff --unreleased
```

To build locally:

```bash
make build
# output: dist/VideoAnnotation/VideoAnnotation
```

Requires `ffmpeg` on PATH and all dev dependencies installed.
