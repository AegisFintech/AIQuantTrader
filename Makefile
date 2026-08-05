UV ?= uv

.PHONY: native-lock native-sync native-format native-lint native-typecheck native-test native-schema-check native-ci native-image

native-lock:
	$(UV) lock

native-sync:
	$(UV) sync --frozen --group dev

native-format:
	$(UV) run --frozen ruff format src tests scripts

native-lint:
	$(UV) run --frozen ruff format --check src tests scripts
	$(UV) run --frozen ruff check src tests scripts

native-typecheck:
	$(UV) run --frozen mypy

native-test:
	$(UV) run --frozen pytest --cov

native-schema-check:
	$(UV) run --frozen aqt-native export-schemas --output schemas --check

native-ci: native-lint native-typecheck native-test native-schema-check
	$(UV) export --frozen --all-extras --no-dev --no-emit-project --output-file /tmp/aiquanttrader-native-audit.txt
	$(UV) run --frozen pip-audit --requirement /tmp/aiquanttrader-native-audit.txt
	./scripts/check_secrets.sh

native-image:
	docker build --tag aiquanttrader-native-foundation:0.1.0 .
