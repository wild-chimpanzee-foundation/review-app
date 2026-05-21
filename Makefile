.PHONY: test coverage lint format run build ci changelog

ci:
	uv run ruff check review_app/ tests/
	uv run ruff format --check review_app/ tests/
	uv run pytest tests/

test:
	uv run pytest tests/

coverage:
	uv run pytest tests/ --cov=review_app/backend --cov-report=term-missing

lint:
	uv run ruff check review_app/ tests/ --fix

format:
	uv run ruff format review_app/

run:
	uv run python -m review_app.app.entry_point

dev:
	uv run python -m review_app.app.entry_point --dev

build:
	uv run pyinstaller video_annotation.spec --clean

changelog:
	uvx git-cliff --unreleased
