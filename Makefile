PY ?= python3
export PYTHONPATH := src

.PHONY: test lint plan run export status

test:
	$(PY) -m unittest discover -s tests -t . -v

lint:
	ruff check src tests

plan:
	$(PY) -m thfin plan

run:
	$(PY) -m thfin run --limit 25

export:
	$(PY) -m thfin export

status:
	$(PY) -m thfin status
