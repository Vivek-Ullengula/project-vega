.PHONY: lint format test check fix help

help:
	@echo "Project Vega Automation Commands"
	@echo "--------------------------------"
	@echo "make check    - Run complete readonly CI lint, format, and test verification suite."
	@echo "make fix      - Apply automatic code formatting and fix standard ruff lint violations."
	@echo "make lint     - Run standalone ruff linter checks."
	@echo "make format   - Apply standalone code formatting."
	@echo "make test     - Run standalone pytest regression framework execution."

check:
	python scripts/pre_push_check.py

fix:
	python scripts/pre_push_check.py --fix

lint:
	python -m ruff check .

format:
	python -m ruff format .

test:
	python -m pytest
