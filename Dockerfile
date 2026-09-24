# Two stages. The build stage has uv and a compiler; the runtime stage has neither, which is both
# smaller and a smaller attack surface — nothing in the shipped image can compile a wheel.

FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies before source, so editing a Python file does not re-resolve the lock file. `--frozen`
# refuses to update the lock: an image whose dependencies drifted from uv.lock is not the thing that
# was tested.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

# A non-root user with no home and no shell. The process reads spreadsheets from outside the trust
# boundary; it has no business being able to log in or write anywhere but /tmp.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin --uid 10001 bordereaux

WORKDIR /app

COPY --from=build --chown=root:root /app/.venv /app/.venv
COPY --from=build --chown=root:root /app/src /app/src
COPY --from=build --chown=root:root /app/scripts /app/scripts

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Read-only unless a deployment says otherwise, and there is no approver token baked in. An
    # image that shipped one would put the same credential on every deployment that ever ran it.
    BX_READ_ONLY=true

USER bordereaux
EXPOSE 8000

# Answers 200 with a database and 503 without one. Both are answers; a container that hung would
# pass a build check and fail on first deploy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status in (200, 503) else 1)"

CMD ["uvicorn", "bordereaux_reconciler.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
