.PHONY: install test lint build clean publish help

help:
	@echo "pyupcheck developer commands"
	@echo ""
	@echo "  make install     Install in editable mode with dev deps"
	@echo "  make test        Run the test suite"
	@echo "  make lint        Run pyflakes linter"
	@echo "  make build       Build distribution packages"
	@echo "  make clean       Remove build artifacts"
	@echo "  make publish     Build and upload to PyPI (set PYPI_TOKEN)"

install:
	pip install -e .
	pip install pytest pyflakes

test:
	pytest tests/ -v

lint:
	python -m pyflakes depshift/

build: clean
	python -m build --no-isolation

clean:
	rm -rf dist/ build/ *.egg-info/ depshift/__pycache__/ tests/__pycache__/ .pytest_cache/

publish: build
	twine upload dist/* -u __token__ -p $(PYPI_TOKEN)
