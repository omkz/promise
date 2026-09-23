FROM python:3.12-slim
# Pinned uv 0.12.18 by tag+digest (immutable): matches the version this repo
# resolves/locks with locally and in CI (see .github/workflows/ci.yml).
COPY --from=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer, cached across builds as long as no pyproject.toml changes:
# only the workspace manifests are present, no application source yet, so `uv
# sync --no-install-project` resolves and installs every third-party
# dependency without needing the code that would otherwise bust this layer on
# every source edit.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/domain/pyproject.toml packages/domain/pyproject.toml
COPY packages/integrations/pyproject.toml packages/integrations/pyproject.toml
COPY packages/agent/pyproject.toml packages/agent/pyproject.toml
COPY packages/app/pyproject.toml packages/app/pyproject.toml
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/mcp/pyproject.toml services/mcp/pyproject.toml
RUN uv sync --locked --no-install-project --no-dev --package promise-service-api

# Application layer: the actual source, then a fast second sync that just
# installs the local workspace packages editable (third-party deps are
# already satisfied by the cached layer above).
COPY packages/shared packages/shared
COPY packages/domain packages/domain
COPY packages/integrations packages/integrations
COPY packages/agent packages/agent
COPY packages/app packages/app
COPY services/api services/api
COPY services/mcp services/mcp
RUN uv sync --locked --no-dev --package promise-service-api

ENV LOCAL_DATA_DIR=/app/data
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "promise_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
