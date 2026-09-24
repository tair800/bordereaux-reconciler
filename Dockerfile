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

# The migrations. Without these the container can bring up its own schema only through
# `create_all`, which is the test path — the deployed service must use the same migrations an
# operator runs, or the thing that is deployed is not the thing that was tested.
COPY --chown=root:root alembic.ini ./alembic.ini
COPY --chown=root:root alembic ./alembic

# The evidence. `/evidence` reads these eight files and nothing else, so it is the one screen that
# works with no database at all — and it is the screen a reader should look at first. Leaving them
# out would have deployed a console that reports "not measured" for every figure the README quotes.
COPY --chown=root:root artifacts ./artifacts

COPY --chown=root:root docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# `chmod` explicitly rather than trusting the mode git recorded. The first deploy of this image
# exited 128 for exactly this reason: the file was committed 100644 from a Windows checkout, the
# local build worked because the working copy happened to carry the bit from a `chmod`, and the
# platform — which checks out from git — got a script it could not execute. The git mode is now
# 100755 as well, and a test asserts it; this line means the image is correct even when it is not.
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

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
#
# `--start-period=45s` rather than 10s: the entrypoint now waits for the database, migrates and
# seeds before uvicorn binds, and a managed Postgres can take most of a minute to accept its first
# connection. A start period shorter than the work it covers marks a healthy container unhealthy.
#
# `PORT` is read from the environment because the platform assigns it. Hard-coding 8000 here would
# have the health check probe a port nothing is listening on, and the container would be killed and
# restarted forever while serving correctly on the port it was told to use.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=4).status in (200, 503) else 1)"

# `PORT` because the platform assigns it; 8000 when nothing does.
CMD ["/usr/local/bin/docker-entrypoint.sh"]
