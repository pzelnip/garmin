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
