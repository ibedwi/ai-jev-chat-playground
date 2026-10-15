FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /uvx /bin/
WORKDIR /app
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
RUN useradd --create-home --uid 10001 app
COPY --chown=app:app app ./app
USER app
EXPOSE 8000
# State is in memory: use one worker so requests share Sessions and Agendas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
