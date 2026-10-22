PY ?= .venv/bin/python

.PHONY: setup test lint bench report
setup:
	python3 -m venv .venv
	$(PY) -m pip install -U pip
	$(PY) -m pip install -e ".[chat,data,dev]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests
	$(PY) -m mypy src tests

# Example: make bench TASK=banking77 BACKEND=jev LIMIT=500
bench:
	$(PY) -m jev_bench.cli run --task $(TASK) --backend $(BACKEND) --limit $(LIMIT)

report:
	$(PY) -m jev_bench.cli report
