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
make run
# or
uv run python -m review_app.app.entry_point --dev
```

`--dev` enables hot reload and logs to the console in addition to the log file.

## Common tasks

| Command | What it does |
|---------|-------------|
| `make test` | Run the test suite |
| `make coverage` | Tests with line coverage report (backend only) |
| `make lint` | Ruff lint check |
| `make format` | Auto-format with ruff |
| `make ci` | Lint + format check + tests (mirrors CI) |
| `make build` | Build standalone executable via PyInstaller |
| `make bump` | Bump version based on conventional commits since last tag |
| `make release` | Bump version, commit, and tag — ready to push |

## Tests

Tests live in `tests/` and use pytest. They exercise the backend only — the NiceGUI frontend has no automated tests.

Run them:

```bash
uv run pytest tests/
```

The test suite spins up an in-memory SQLite database per test, so no external state is required.

When adding backend functionality, add a corresponding test. The existing files are a reasonable guide for where things belong:

| File | Covers |
|------|--------|
| `test_migrations.py` | Migration runner, idempotency |
| `test_project_and_import.py` | Project CRUD, model CSV import |
| `test_queue_and_export.py` | Video queue filtering, annotation export |
| `test_provider_misc.py` | Settings, video sync, reprobe |
| `test_species.py` | Species/behavior loading and matching |

## Database migrations

Migrations live in `review_app/backend/migrations.py` as a versioned list. The rules:

- **Never modify or remove an existing entry** — migrations are applied once per database and tracked by version number.
- To make a schema change, append a new `(version, sql)` entry. Versions must be contiguous starting at 1.
- Use a callable (instead of a SQL string) when the migration needs conditional logic or multiple steps — see `_migration_v4` for an example.
- Write a test in `test_migrations.py` that applies migrations to a fresh DB and asserts the expected schema.

## Code layout

```
review_app/
  app/           # UI layer (NiceGUI pages, state, translations)
    pages/       # One file per page (overview, review, settings, model_import)
  backend/       # Data layer — no UI imports allowed here
    models.py    # SQLAlchemy schema
    migrations.py
    local_data_provider.py  # Main data access class
    video.py     # ffprobe probing and transcoding (VideoMixin)
    species.py   # Fuzzy matching and species queries (SpeciesMixin)
    backup.py    # Backup/restore with dedicated exception hierarchy
```

`LocalDataProvider` composes `VideoMixin` and `SpeciesMixin` via multiple inheritance. Keep backend modules free of NiceGUI imports.

## Linting and formatting

Ruff is configured in `pyproject.toml` (line length 99, isort enabled). The pre-commit hook auto-fixes and formats on commit. To check manually:

```bash
make lint      # check only
make format    # fix in place
```

Pre-commit hooks enforce both on every commit. Run `make ci` before pushing to catch anything the hooks missed.

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
