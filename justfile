set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

sandbox := "--manifest-path sandbox/Cargo.toml"

# List recipes
default:
    @just --list

# Install Python, WebUI, and Rust dependencies
setup:
    uv sync --frozen
    pnpm --dir webui install --frozen-lockfile
    cargo fetch --locked {{sandbox}}

# Backend on :6185 plus the Vite dev server on :5173 (proxies /api)
dev *args:
    uv run python scripts/dev.py {{args}}

# Every gate CI runs on Linux and Windows
check: lint typecheck test codegen-check bundle-check

lint:
    uv run ruff check .
    uv run ruff format --check .
    cargo fmt --all --check {{sandbox}}
    cargo clippy --workspace --all-targets --locked {{sandbox}} -- -D warnings
    pnpm --dir webui lint

typecheck:
    uv run pyright
    pnpm --dir webui typecheck

test: build-sandbox
    cargo test --workspace --locked {{sandbox}}
    uv run pytest --cov
    pnpm --dir webui test

build-sandbox:
    cargo build --locked {{sandbox}}

build-webui:
    pnpm --dir webui build

# First-screen JavaScript budget (docs/DEV_PLAN.md 8.2)
bundle-check: build-webui
    pnpm --dir webui check:bundle

# Regenerate the OpenAPI client and sandbox protocol models
codegen:
    uv run python scripts/codegen.py

codegen-check:
    uv run python scripts/codegen.py --check

# Runtime benchmarks with budgets
bench *args:
    uv run python -m benchmarks.run {{args}}

# Playwright on the host; screenshots can differ from CI because of local fonts
e2e *args: build-webui build-sandbox
    pnpm --dir webui e2e {{args}}

# Playwright in the pinned Playwright image, identical to CI (Linux/macOS)
e2e-docker *args: build-webui
    bash scripts/playwright-docker.sh {{args}}

# Re-render visual baselines in the pinned Playwright image
update-snapshots: build-webui
    bash scripts/playwright-docker.sh visual --update-snapshots

# License, advisory, and source checks for Rust dependencies
deny:
    cargo deny {{sandbox}} check
