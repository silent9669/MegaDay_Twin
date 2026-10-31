FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --locked --no-dev
COPY backend/app ./app
# Explicit historical inputs only. No replay future rows, evaluator data or local DB.
COPY mock-data/out/demo/*.csv mock-data/out/demo/cau_truc_chien_dich.json mock-data/out/demo/chuong_trinh_sale.json /seed-input/
RUN DUCKDB_PATH=/seed/megaday.duckdb AI_ENABLED=false uv run --no-sync python -m app.cli migrate
RUN DUCKDB_PATH=/seed/megaday.duckdb AI_ENABLED=false uv run --no-sync python -m app.cli ingest --dir /seed-input
RUN useradd --uid 10001 --create-home megaday && mkdir /state && chown megaday:megaday /state
USER megaday
EXPOSE 8000
CMD ["/app/.venv/bin/python", "-m", "app.hosted"]
