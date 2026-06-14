.PHONY: update-dependencies
update-dependencies: ## Upgrade locked dependencies
	uv lock --upgrade

.PHONY: db-session
db-session: ## Start a database session
	./scripts/db_sess.sh

.PHONY: pull-and-post
pull-and-post: ## Pull data from Garmin into the DB
	cd src && python garmin.py $(ARGS)

.PHONY: run-server
run-server: ## Run the Flask server
	cd src && python app.py

.PHONY: test
test: ## Run the test suite with pytest
	uv run pytest

.PHONY: test-coverage
test-coverage: ## Run the test suite and generate an HTML coverage report
	uv run pytest --cov=src --cov-report=html --cov-report=term

.PHONY: ruff
ruff: ## Lint the Python code with ruff
	uv run ruff check src

.PHONY: pylint
pylint: ## Lint the Python code with pylint
	uv run pylint src

.PHONY: lint
lint: ruff pylint ## Lint the Python code with ruff & pylint

.PHONY: black
black: ## Format the Python code with black
	uv run black src

.PHONY: isort
isort: ## Format the Python code with isort
	uv run isort src

.PHONY: format
format: black isort ## Format the Python code with black & isort
