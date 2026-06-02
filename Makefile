.PHONY: install dev setup test clean help

help:
	@echo "Usage:"
	@echo "  make install    Install rtmux CLI"
	@echo "  make dev        Install in development mode"
	@echo "  make setup      Run environment setup (venv + paramiko + rtmux)"
	@echo "  make test       Run tests"
	@echo "  make clean      Clean build artifacts"

install:
	pip install .

dev:
	pip install -e ".[dev]"

setup:
	python3 setup_rtmux.py

test:
	pytest tests/ -v

clean:
	rm -rf build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
