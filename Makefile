.PHONY: test coverage lint format run build ci changelog bump release docs docs-build

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
	uv run ruff format review_app/ tests/ scripts/

run:
	uv run python -m review_app.app.entry_point

dev:
	uv run python -m review_app.app.entry_point --dev

build:
	uv run pyinstaller video_annotation.spec --clean

changelog:
	uvx git-cliff --unreleased

bump:
	$(eval VERSION ?= $(shell uvx git-cliff --bumped-version 2>/dev/null | sed 's/^v//'))
	@[ -n "$(VERSION)" ] || (echo "Could not determine next version from commits" && exit 1)
	sed -i 's/__version__ = ".*"/__version__ = "$(VERSION)"/' review_app/__init__.py
	@echo "Version bumped to $(VERSION)"

docs:
	uv run mkdocs serve

docs-build:
	uv run mkdocs build --strict

release:
	$(eval VERSION ?= $(shell uvx git-cliff --bumped-version 2>/dev/null | sed 's/^v//'))
	@[ -n "$(VERSION)" ] || (echo "Could not determine next version from commits" && exit 1)
	sed -i 's/__version__ = ".*"/__version__ = "$(VERSION)"/' review_app/__init__.py
	git add review_app/__init__.py
	git commit -m "chore: release $(VERSION)"
	git tag "v$(VERSION)"
	@echo "Tagged v$(VERSION) — push with: git push origin main v$(VERSION)"
