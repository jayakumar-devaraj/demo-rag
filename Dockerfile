FROM python:3.13.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev
# Bake the embedding model in so startup needs no network and the non-root user needs no writable cache.
ARG FASTEMBED_MODEL=BAAI/bge-small-en-v1.5
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN /opt/venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('$FASTEMBED_MODEL')"

FROM python:3.13.13-slim-bookworm AS migrate
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --only-group migrate
RUN useradd --system --uid 10001 --no-create-home rag
COPY alembic.ini ./
COPY migrations ./migrations
USER rag
CMD ["alembic", "upgrade", "head"]

FROM python:3.13.13-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/jayakumar-devaraj/multi-tenant-agentic-rag"
RUN useradd --system --uid 10001 --no-create-home rag
ENV PATH=/opt/venv/bin:$PATH FASTEMBED_CACHE_PATH=/opt/fastembed HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/fastembed /opt/fastembed
WORKDIR /app
COPY src ./src
USER rag
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["python", "-m", "src.main"]
