FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
RUN mkdir -p /app/data
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "python", "-m", "app.main"]
