.PHONY: update-dependencies
update-dependencies: ## Update the requirements.txt file
	pip-compile --upgrade --generate-hashes --output-file requirements.txt requirements.in

.PHONY: db-session
db-session: ## Start a database session
	./db_sess.sh

.PHONY: pull-and-post
pull-and-post: ## Pull data from Garmin into the DB, and prompt to post to channel
	python garmin.py

.PHONY: daily-query
daily-query: ## Run the daily step counts query
	./query.sh

.PHONY: monthly-post
monthly-post: ## Pull data from Garmin into the DB, and prompt to post to channel
	python monthly_post.py
