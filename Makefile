BOOKS_DIR ?= /mnt/c/tsundokensaku-books/tech
DB_PATH ?= data/index.db

.PHONY: build run index search test shell

build:
	docker compose build

run:
	docker compose run --rm app index --books-dir /books/tech --db $(DB_PATH)

index:
	docker compose run --rm app index --books-dir /books/tech --db $(DB_PATH)

search:
	docker compose run --rm app search --db $(DB_PATH) "$(QUERY)"

test:
	docker compose run --rm app python -m unittest discover -s tests

shell:
	docker compose run --rm --entrypoint bash app
