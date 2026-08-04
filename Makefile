UV ?= uv

.PHONY: native-lock native-sync native-format native-lint native-typecheck native-test native-schema-check native-ci native-image

native-lock:
	cd native && $(UV) lock

native-sync:
	cd native && $(UV) sync --frozen --group dev

native-format:
	cd native && $(UV) run --frozen ruff format src tests scripts

native-lint:
	cd native && $(UV) run --frozen ruff format --check src tests scripts
	cd native && $(UV) run --frozen ruff check src tests scripts

native-typecheck:
	cd native && $(UV) run --frozen mypy

native-test:
	cd native && $(UV) run --frozen pytest --cov

native-schema-check:
	cd native && $(UV) run --frozen aqt-native export-schemas --output schemas --check

native-ci: native-lint native-typecheck native-test native-schema-check
	cd native && $(UV) export --frozen --all-extras --no-dev --no-emit-project --output-file /tmp/aiquanttrader-native-audit.txt
	cd native && $(UV) run --frozen pip-audit --requirement /tmp/aiquanttrader-native-audit.txt
	./scripts/check_secrets.sh

native-image:
	docker build --tag aiquanttrader-native-foundation:0.1.0 native
