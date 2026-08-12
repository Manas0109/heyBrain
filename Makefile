.PHONY: install test run doctor

install:
	pip install -e ".[dev]"

test:
	pytest

run:
	brain

doctor:
	brain doctor
