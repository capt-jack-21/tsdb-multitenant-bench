.PHONY: up down gen bench report clean

up:
	@echo "TODO Day 3: docker compose up -d"

down:
	@echo "TODO Day 3: docker compose down -v"

gen:
	@echo "TODO Day 2: python bench/generate.py"

bench:
	@echo "TODO Days 4-9: python bench/run.py"

report:
	@echo "TODO Day 11: python bench/report.py"

clean:
	rm -rf data/ __pycache__ .venv
