.PHONY: install demo serve test evaluate check clean

install:
	python3 -m pip install -r requirements-dev.txt

demo:            ## build the simulated city and replay to 2026-10-10 (about 10-15 minutes)
	python3 -m shield.cli seed

demo-live:       ## seed and stop before the first alert, to advance live during a presentation
	python3 -m shield.cli seed --backfill-end 2026-09-10 --clock 2026-09-10

serve:
	python3 -m shield.cli serve --host 127.0.0.1 --port 8000

test:
	python3 -m pytest

evaluate:
	python3 -m shield.cli evaluate

check:
	python3 -m shield.cli check

clean:
	rm -rf data/
