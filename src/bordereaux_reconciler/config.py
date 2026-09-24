"""Configuration, read once, validated at import, with no silent defaults for anything dangerous.

Two settings carry real weight and both fail closed.

`read_only` defaults to **true**. A deployment that forgot to configure anything can be read and
cannot be written to, which is the right way round: the cost of an over-restrictive default is
somebody files a bug, and the cost of an over-permissive one is an anonymous visitor confirming a
mapping that decides how a coverholder's premium is read.

`approver_token` has **no default at all**. When it is unset the write endpoints do not merely
reject requests, they are not mounted, so there is no token to guess and nothing to time-attack. The
public demo runs exactly this way, and the README says so rather than implying a demo can approve.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

__all__ = ["Settings", "get_settings"]


def _flag(name: str, *, default: bool) -> bool:
    """A boolean from the environment, strictly.

    Anything other than a recognised true or false value is an error rather than a fallback. A
    `BX_READ_ONLY=flase` that quietly read as true would be harmless; the same typo on a setting
    that defaulted the other way would not, and a parser that guesses is the wrong habit to have in
    one place and not the other.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean; use true or false")


@dataclass(frozen=True)
class Settings:
    database_url: str
    read_only: bool
    approver_token: str | None
    corpus_dir: str
    artifacts_dir: str
    #: Shown in the console header so a reader knows which of these they are looking at.
    environment: str

    @property
    def writes_enabled(self) -> bool:
        """Both conditions, not either. An approver token does not overrule read-only mode."""
        return not self.read_only and bool(self.approver_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    from bordereaux_reconciler.store import DEFAULT_DATABASE_URL  # noqa: PLC0415 - avoids a cycle

    return Settings(
        database_url=os.environ.get("BX_DATABASE_URL", DEFAULT_DATABASE_URL),
        read_only=_flag("BX_READ_ONLY", default=True),
        approver_token=os.environ.get("BX_APPROVER_TOKEN") or None,
        corpus_dir=os.environ.get("BX_CORPUS_DIR", "data/generated"),
        artifacts_dir=os.environ.get("BX_ARTIFACTS_DIR", "artifacts"),
        environment=os.environ.get("BX_ENVIRONMENT", "local"),
    )
