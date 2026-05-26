.PHONY: test validate-example dry-run

test:
	pytest

validate-example:
	python -m sitegraph.cli validate-config examples/sites/jwc/config/site.yaml

dry-run:
	python -m sitegraph.cli crawl-site examples/sites/jwc/config/site.yaml --dry-run
