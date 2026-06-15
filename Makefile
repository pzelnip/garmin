.PHONY: update-dependencies
update-dependencies: ## Upgrade locked dependencies
	uv lock --upgrade
	uv sync

.PHONY: db-session
db-session: ## Start a database session
	./scripts/db_sess.sh

.PHONY: pull-and-post
pull-and-post: ## Pull data from Garmin into the DB
	uv run python src/garmin.py $(ARGS)

.PHONY: run-server
run-server: ## Run the Flask server
	uv run python src/app.py

.PHONY: test
test: ## Run the test suite with pytest
	uv run pytest

.PHONY: test-coverage
test-coverage: ## Run the test suite and generate an HTML coverage report
	uv run pytest --cov=src --cov-report=html --cov-report=term

.PHONY: ruff
ruff: ## Lint the Python code with ruff
	uv run ruff check src

.PHONY: lint
lint: ruff ## Run all linters

.PHONY: black
black: ## Format the Python code with black
	uv run black src

.PHONY: isort
isort: ## Format the Python code with isort
	uv run isort src

.PHONY: format
format: black isort ## Format the Python code with black & isort
