"""Capture the console's screens into `docs/screenshots/`.

    uv run playwright install chromium          # once
    uv run python scripts/screenshots.py        # against a locally served console
    uv run python scripts/screenshots.py --base-url https://bordereaux-reconciler.onrender.com

Scripted rather than hand-captured so the images in the README can be regenerated after a change
and be *checked*. A screenshot nobody can reproduce is a claim about a user interface that may no
longer exist.

Each screen is captured twice, light and dark, because the console follows the reader's system theme
and a README showing only one of them is showing half the work.

`--base-url` points the same capture at an already-running service, which is how
`docs/screenshots/live/` is produced from the public deployment. The README's own images stay the
locally-generated set: those reproduce on any machine from this repository, and a free-tier URL that
sleeps after fifteen minutes does not.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"

#: Wide enough for the evidence tables to breathe, narrow enough to stay legible in a README.
WIDTH, HEIGHT = 1280, 1000

#: Given up on after this long. A console that has not answered in 45 seconds is broken — except on
#: a free instance that has gone to sleep, which is why `--base-url` gets a longer wait below.
BOOT_TIMEOUT_SECONDS = 45

#: A cold free instance takes the better part of a minute to wake, and the first request pays for
#: it. Waiting is the correct behaviour here; failing would only mean running the script again.
WAKE_TIMEOUT_SECONDS = 180

#: The screens, in the order a reader should meet them. `/rows/` is resolved at capture time
#: because the row identifiers depend on how many times the database has been seeded.
SCREENS: tuple[tuple[str, str], ...] = (
    ("overview", "/"),
    ("evidence", "/evidence"),
    ("mappings", "/mappings"),
    ("quarantine", "/quarantine"),
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(url: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with (
            contextlib.suppress(urllib.error.URLError, ConnectionError, OSError),
            # S310: a URL this script was given or built, not a scheme from untrusted input.
            urllib.request.urlopen(url, timeout=10) as response,  # noqa: S310
        ):
            if response.status in (200, 503):
                return
        time.sleep(2)
    raise SystemExit(f"{url} never answered within {timeout}s")


def _first_row_path(base: str) -> str | None:
    """A ledger row that exists, found by walking the console rather than the database.

    The identifiers move every time the demo database is reseeded, so hard-coding one would
    produce a 404 screenshot the first time anybody regenerated these.
    """
    import re  # noqa: PLC0415

    with urllib.request.urlopen(f"{base}/", timeout=30) as response:  # noqa: S310
        overview = response.read().decode("utf-8", "replace")

    for match in re.finditer(r"files/([a-f0-9]{64})", overview):
        with urllib.request.urlopen(  # noqa: S310
            f"{base}/files/{match.group(1)}", timeout=30
        ) as response:
            detail = response.read().decode("utf-8", "replace")
        if row := re.search(r"rows/(\d+)", detail):
            return f"/rows/{row.group(1)}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SHOTS)
    parser.add_argument(
        "--base-url",
        help="capture an already-running console instead of starting one",
    )
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    server: subprocess.Popen[bytes] | None = None
    if args.base_url:
        base = args.base_url.rstrip("/")
        print(f"capturing {base} (waiting for a cold instance to wake)")
        _wait_for(f"{base}/healthz", WAKE_TIMEOUT_SECONDS)
    else:
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        # Read-only and with no approver token, exactly as the public deployment runs. Capturing a
        # console that could accept a write would picture a deployment nobody gets.
        environment = dict(os.environ) | {
            "BX_READ_ONLY": "true",
            "BX_ENVIRONMENT": "local",
        }
        environment.pop("BX_APPROVER_TOKEN", None)
        server = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-m",
                "uvicorn",
                "bordereaux_reconciler.api.app:app",
                "--port",
                str(port),
                "--log-level",
                "error",
            ],
            cwd=ROOT,
            env=environment,
        )
        _wait_for(f"{base}/healthz", BOOT_TIMEOUT_SECONDS)

    screens = list(SCREENS)
    if row := _first_row_path(base):
        screens.insert(2, ("row-lineage", row))
    else:
        print("no ledger row found; skipping the lineage screen")

    # Resolved, because `--out docs/screenshots/live` arrives relative and the progress line below
    # computes a path relative to the repository root. An absolute destination is also the safer
    # thing to hand to a browser process with its own working directory.
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for scheme in ("light", "dark"):
                context = browser.new_context(
                    viewport={"width": WIDTH, "height": HEIGHT}, color_scheme=scheme
                )
                page = context.new_page()
                for name, path in screens:
                    page.goto(f"{base}{path}", wait_until="networkidle", timeout=60_000)
                    destination = out / f"{name}-{scheme}.png"
                    page.screenshot(path=str(destination), full_page=True)
                    print(f"  {destination.relative_to(ROOT)}")
                context.close()
            browser.close()
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=10)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
