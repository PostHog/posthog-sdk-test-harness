FROM python:3.12.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock CONTRACT.yaml ./
COPY contracts/ ./contracts/
COPY src/ ./src/

# Install the test harness
RUN uv sync --frozen

# Set up entry point
ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["uv", "run", "posthog-test-harness"]
