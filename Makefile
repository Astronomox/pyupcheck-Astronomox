.PHONY: install test lint build clean release help bench

help:
	@echo "pyupcheck developer commands"
	@echo ""
	@echo "  make install     Install in editable mode with dev deps"
	@echo "  make test        Run the test suite"
	@echo "  make lint        Run ruff and mypy"
	@echo "  make bench       Run the accuracy benchmark (needs network)"
	@echo "  make build       Build distribution packages"
	@echo "  make clean       Remove build artifacts"
	@echo "  make release     How to publish a release (see RELEASING.md)"

install:
	pip install -e .
	pip install pytest pytest-cov ruff mypy

test:
	pytest tests/ --cov=depshift --cov-report=term-missing:skip-covered --cov-fail-under=89

bench:
	python benchmarks/run.py

lint:
	python -m ruff check depshift tests examples scripts
	python -m mypy

build: clean
	python -m build --no-isolation

clean:
	rm -rf dist/ build/ *.egg-info/ depshift/__pycache__/ tests/__pycache__/ .pytest_cache/

release:
	@echo "Releases are published by GitHub Actions when a version tag is pushed:"
	@echo "  python bump.py minor && git commit -am 'Release X.Y.Z'"
	@echo "  git tag vX.Y.Z && git push origin main vX.Y.Z"
	@echo "See RELEASING.md."
