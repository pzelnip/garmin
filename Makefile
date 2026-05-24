.PHONY: update-dependencies
update-dependencies: ## Upgrade locked dependencies
	uv lock --upgrade

.PHONY: db-session
db-session: ## Start a database session
	./scripts/db_sess.sh

.PHONY: pull-and-post
pull-and-post: ## Pull data from Garmin into the DB
	cd src && python garmin.py $(ARGS)

.PHONY: daily-query
daily-query: ## Run the daily step counts query
	./query.sh

.PHONY: monthly-post
monthly-post: ## Pull data from Garmin into the DB, and prompt to post to channel
	cd src && python monthly_post.py

.PHONY: run-server
run-server: ## Run the Flask server
	cd src && python app.py
