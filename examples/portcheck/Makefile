install:
	pip install -r requirements.txt

run:
	PYTHONPATH=. python main.py

test:
	PYTHONPATH=. pytest

clean:
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -exec rm -rf {} +
	rm -f portcheck.egg-info
	rm -f dist/*.*gz
	rm -f dist/*.whl
	rm -f *.log
	rm -f coverage.xml
	rm -f htmlcov/ -R
	rm -f .coverage
	rm -f .coverage.*
	rm -f .pytest_cache/
	rm -f .mypy_cache/
	rm -f .tox/
	rm -f .venv/
	rm -f .idea/
	rm -f .vscode/
	rm -f .DS_Store
	rm -f *~
	rm -f .*
rm:
	rm -rf build/ dist/ egg-info/ __pycache__/ *.pyc *.log coverage.xml htmlcov/ .coverage .coverage.* .pytest_cache/ .mypy_cache/ .tox/ .venv/ .idea/ .vscode/ .DS_Store *~ .* PORTS.txt
.PHONY: install run test clean rm
