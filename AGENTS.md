# Required Operational Guidelines
**Setup/Execution:**
*   **Dashboard Run:** Use `uv run streamlit run review_app/frontend/1_Pipeline_Overview.py`.
*   **Testing:** Run targeted tests with `uv run pytest review_app/`.
*   **Build/Container:** Use `docker compose up --build`.
*   **Environment:** Ensure `.env` sets `REVIEW_APP_USER_EMAIL` before running. The database is automatically managed via `config.yaml` using a local SQLite file.
**Style & Convention:**
*   **Code Style:** 4-space indent, `snake_case`.
*   **Linting:** Run `uv run ruff check review_app` or `uv run ruff lint review_app` in CI.
**Data/Source of Truth:**
*   `config.yaml` sources: `video_dir`, `db_dir`, species/behavior lists. Update these before new sessions.
*   Secrets: Never commit secrets (e.g., `.env`, credentials).
**Workflow Quirks:**
*   Schema updates: Always capture table/column changes from `_local_db` before committing snapshots.
*   Commits: Follow short, lowercase, present-tense summaries.
