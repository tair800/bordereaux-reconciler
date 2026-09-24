"""The deployment contract: things that are only true in a real container.

Every test here exists because of a failure that reading could not have found. The file-mode one is
the clearest: the first Render deploy of this image exited 128 because `docker-entrypoint.sh` was
committed non-executable from a Windows checkout, while the local build worked because the working
copy carried a `chmod` that git had not recorded.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_the_entrypoint_is_committed_executable() -> None:
    """Git mode 100755, not 100644. The Dockerfile also chmods it; both, deliberately.

    A shell script committed 100644 lands non-executable on any platform that checks out from git,
    and the container exits 128 with no message naming the file.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-s", "docker-entrypoint.sh"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listed.startswith("100755"), (
        f"docker-entrypoint.sh is committed as {listed.split()[0]}. It must be 100755, or a "
        "checkout on the deployment platform produces a script the container cannot execute."
    )


def test_the_dockerfile_chmods_the_entrypoint_anyway() -> None:
    """Belt and braces. The git mode is one bit in one place, and it has already been wrong once."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"RUN\s+chmod\s+0?755\s+/usr/local/bin/docker-entrypoint\.sh", dockerfile)


def test_the_entrypoint_has_unix_line_endings_in_git() -> None:
    """A CRLF shell script fails with `bad interpreter: /bin/sh^M`, which names nothing useful."""
    eol = subprocess.run(
        ["git", "ls-files", "--eol", "docker-entrypoint.sh"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "i/lf" in eol, f"the committed blob is not LF: {eol.strip()}"


class TestTheBlueprint:
    @pytest.fixture
    def blueprint(self) -> dict:
        return yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))

    def test_the_public_service_is_free(self, blueprint: dict) -> None:
        assert blueprint["services"][0]["plan"] == "free"
        assert blueprint["databases"][0]["plan"] == "free"

    def test_the_public_service_is_read_only(self, blueprint: dict) -> None:
        env = {e["key"]: e.get("value") for e in blueprint["services"][0]["envVars"]}
        assert env["BX_READ_ONLY"] == "true"

    def test_no_approver_token_is_declared(self, blueprint: dict) -> None:
        """Its absence is what makes the write endpoints refuse. Adding one here would put the
        same credential on every deploy of this blueprint."""
        keys = {e["key"] for e in blueprint["services"][0]["envVars"]}
        assert "BX_APPROVER_TOKEN" not in keys

    def test_no_model_credential_is_declared(self, blueprint: dict) -> None:
        """A public endpoint in front of a paid API is a bill waiting to be run up."""
        keys = {e["key"] for e in blueprint["services"][0]["envVars"]}
        assert not any("API_KEY" in k or "TOKEN" in k for k in keys), sorted(keys)

    def test_the_health_check_path_is_the_one_the_app_serves(self, blueprint: dict) -> None:
        assert blueprint["services"][0]["healthCheckPath"] == "/healthz"

    def test_the_database_version_is_the_one_that_was_tested(self, blueprint: dict) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "postgres:16" in compose
        assert str(blueprint["databases"][0]["postgresMajorVersion"]) == "16"
