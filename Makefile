PYTHON ?= $(CURDIR)/.venv/bin/python
BENCH_OUTPUT ?= $(CURDIR)/artifacts/matrix

.PHONY: check-spec check-package-data test-conformance test-python test-node test-go test-rust check test-proxy test-updates bench-smoke test-package-install test-python-package test-node-package test-go-package test-rust-package test-release

check-spec:
	$(PYTHON) scripts/check_spec.py
	$(PYTHON) -m ruff check --config python/pyproject.toml scripts benchmarks examples tests/integration/python_consumer.py tests/live
	$(PYTHON) -m ruff format --check --config python/pyproject.toml scripts benchmarks examples tests/integration/python_consumer.py tests/live
	$(PYTHON) -m unittest discover -s benchmarks -p 'test_*.py'
	$(PYTHON) -m unittest discover -s tests/live -p 'test_*.py'

check-package-data:
	$(PYTHON) scripts/sync_package_data.py --check

test-conformance: check-spec check-package-data test-python test-node test-go test-rust

test-python:
	cd python && $(PYTHON) -m ruff check .
	cd python && $(PYTHON) -m ruff format --check .
	cd python && $(PYTHON) -m mypy --config-file pyproject.toml
	cd python && $(PYTHON) -m pytest -q
	$(PYTHON) -m build python

test-node:
	cd js && npm run lint && npm run typecheck && npm test && npm run build

test-go:
	cd go && test -z "$$(gofmt -l *.go)" && go vet ./... && go test -race ./... && go build ./...

test-rust:
	+cd rust && cargo fmt --check && cargo clippy --all-features --all-targets -- -D warnings
	+cd rust && cargo test --all-features && cargo test --no-default-features && cargo test --no-default-features --features async && cargo test --no-default-features --features blocking
	+cd rust && cargo test --all-features process_refresh_registry_limit_and_cleanup -- --ignored --test-threads=1
	+cd rust && cargo build --all-features

check: test-conformance test-release

test-release:
	$(PYTHON) -m unittest discover -s tests/release -p 'test_*.py' -v

test-proxy:
	cd python && $(PYTHON) -m pytest -q tests/test_proxy.py

test-updates:
	cd python && $(PYTHON) -m pytest -q tests/test_client.py -k 'update or refresh or expir or malformed'

bench-smoke:
	$(PYTHON) benchmarks/python_clients.py

.PHONY: bench-matrix bench-matrix-smoke test-benchmark-adapters
bench-matrix:
	$(PYTHON) benchmarks/run_matrix.py --output $(BENCH_OUTPUT)

bench-matrix-smoke:
	$(PYTHON) benchmarks/run_matrix.py --smoke --output $(BENCH_OUTPUT)

test-benchmark-adapters:
	$(PYTHON) -m unittest discover -s benchmarks -p 'test_*.py'
	cd js && node --test benchmarks/matrix.test.cjs
	cd go/benchmarks && go test -race ./matrix

.PHONY: bench-pressure test-pressure-adapters test-live-helpers prepare-release
.PHONY: test-live
LIVE_OUTPUT ?= $(CURDIR)/artifacts/live-report.json
LIVE_ARGS ?=

test-live: test-live-helpers
	cd js && npm run build
	$(PYTHON) scripts/check_demo_matrix.py --output $(LIVE_OUTPUT) $(LIVE_ARGS)

test-pressure-adapters:
	cd benchmarks/pressure/go && test -z "$$(gofmt -l *.go)" && go vet ./... && go test -race ./... && go build -o pressure-go .
	cargo fmt --manifest-path benchmarks/pressure/rust/Cargo.toml --check
	cargo clippy --locked --manifest-path benchmarks/pressure/rust/Cargo.toml -- -D warnings
	cargo build --locked --release --manifest-path benchmarks/pressure/rust/Cargo.toml

test-live-helpers:
	node --test tests/live/node.test.cjs
	cd tests/live/go && test -z "$$(gofmt -l *.go)" && go vet ./... && go build -o ../../../artifacts/live/go .
	cargo fmt --manifest-path tests/live/rust/Cargo.toml --check
	cargo clippy --locked --manifest-path tests/live/rust/Cargo.toml -- -D warnings
	cargo build --locked --manifest-path tests/live/rust/Cargo.toml

bench-pressure: test-pressure-adapters
	cd js && npm run build
	$(PYTHON) benchmarks/pressure/run.py --output $(BENCH_OUTPUT)
	$(PYTHON) benchmarks/pressure/report.py $(BENCH_OUTPUT)/raw.json --exit-record $(BENCH_OUTPUT)/exit-record.json --output $(BENCH_OUTPUT)/summary.json

prepare-release:
	$(PYTHON) scripts/release.py prepare --version 0.1.1 --output $(CURDIR)/artifacts/release-0.1.1
	$(PYTHON) scripts/release.py verify --output $(CURDIR)/artifacts/release-0.1.1
	release_check_dir=$$(mktemp -d /tmp/anchorpass-release-check.XXXXXX) && $(PYTHON) scripts/release_check.py --output $(CURDIR)/artifacts/release-0.1.1 --work-dir "$$release_check_dir/consumer" --timeout 600

test-package-install: test-python-package test-node-package test-go-package test-rust-package

test-python-package:
	$(PYTHON) scripts/check_python_package.py

test-node-package:
	cd js && npm pack && npm run test:package

test-go-package:
	$(PYTHON) scripts/check_go_package.py $(CURDIR)/go

test-rust-package:
	$(PYTHON) scripts/check_rust_package.py
