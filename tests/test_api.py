"""The console: the read-only gate, the unconditional false-MATCHED banner, and the screens.

The gate is tested from three directions — no token, a wrong token, and a right token while
read-only is still set — because "write protection" that only holds in the first case is the kind
that gets deployed by accident.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from bordereaux_reconciler.api.app import app
from bordereaux_reconciler.config import Settings, get_settings
from bordereaux_reconciler.store import create_all, get_engine
from bordereaux_reconciler.store.ledger import reset
from bordereaux_reconciler.store.schema import mapping_contract


def _database_or_skip() -> None:
    try:
        with get_engine().connect() as connection:
            connection.execute(select(1))
    except (OperationalError, OSError) as exc:
        pytest.skip(f"no PostgreSQL reachable ({type(exc).__name__})")


@pytest.fixture
def client() -> Iterator[TestClient]:
    _database_or_skip()
    create_all(get_engine())
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def writable_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    _database_or_skip()
    engine = get_engine()
    create_all(engine)
    reset(engine)
    monkeypatch.setenv("BX_READ_ONLY", "false")
    monkeypatch.setenv("BX_APPROVER_TOKEN", "test-token-not-a-real-secret")
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
    reset(engine)


class TestHealth:
    def test_health_reports_whether_this_instance_can_write(self, client: TestClient) -> None:
        """A check that said only "ok" would be green on an instance that came up writable."""
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["read_only"] is True
        assert body["writes_enabled"] is False
        assert set(body["families"]) == {"insurance", "marketplace"}


class TestScreensRender:
    @pytest.mark.parametrize("path", ["/", "/quarantine", "/mappings", "/evidence"])
    def test_every_screen_returns_html(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<html" in response.text

    def test_a_missing_file_is_404_rather_than_a_stack_trace(self, client: TestClient) -> None:
        assert client.get("/files/" + "0" * 64).status_code == 404

    def test_a_missing_row_is_404(self, client: TestClient) -> None:
        assert client.get("/rows/99999999").status_code == 404

    def test_the_read_only_chip_is_shown(self, client: TestClient) -> None:
        assert "read-only" in client.get("/").text

    def test_the_synthetic_disclaimer_is_on_every_page(self, client: TestClient) -> None:
        """ADR-001 forbids describing the corpus as real. That has to travel with the pages."""
        for path in ("/", "/quarantine", "/mappings", "/evidence"):
            assert "synthetic" in client.get(path).text.lower()


class TestTheFalseMatchedBanner:
    def test_it_renders_whether_or_not_there_are_any(self, client: TestClient) -> None:
        """Unconditional on purpose: a warning that only appears when something is wrong teaches
        nobody where to look, and this is the first number a reader should check."""
        body = client.get("/").text
        assert "False MATCHED" in body


class TestTheWriteGate:
    def test_no_token_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/mappings/Northgate/confirm",
            json={"family": "insurance", "columns": {"Gross Premium": "gross_premium"}},
        )
        assert response.status_code == 403

    def test_a_token_does_not_overrule_read_only_mode(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both conditions, not either. Setting a token on a read-only instance changes nothing."""
        monkeypatch.setenv("BX_APPROVER_TOKEN", "test-token-not-a-real-secret")
        get_settings.cache_clear()
        response = client.post(
            "/mappings/Northgate/confirm",
            headers={"X-Approver-Token": "test-token-not-a-real-secret"},
            json={"family": "insurance", "columns": {"Gross Premium": "gross_premium"}},
        )
        assert response.status_code == 403

    def test_a_wrong_token_is_refused_when_writes_are_enabled(
        self, writable_client: TestClient
    ) -> None:
        response = writable_client.post(
            "/mappings/Northgate/confirm",
            headers={"X-Approver-Token": "not-the-token"},
            json={"family": "insurance", "columns": {"Gross Premium": "gross_premium"}},
        )
        assert response.status_code == 403

    def test_the_right_token_confirms_a_mapping(self, writable_client: TestClient) -> None:
        response = writable_client.post(
            "/mappings/Northgate/confirm",
            headers={"X-Approver-Token": "test-token-not-a-real-secret"},
            json={"family": "insurance", "columns": {"Gross Premium": "gross_premium"}},
        )
        assert response.status_code == 201
        with get_engine().connect() as connection:
            stored = connection.execute(select(mapping_contract)).mappings().all()
        assert len(stored) == 1
        assert stored[0]["coverholder"] == "Northgate"

    def test_the_approver_identity_comes_from_the_token_not_a_header(
        self, writable_client: TestClient
    ) -> None:
        """An `X-Approved-By` the client filled in would put a chosen name in the audit trail."""
        response = writable_client.post(
            "/mappings/Northgate/confirm",
            headers={
                "X-Approver-Token": "test-token-not-a-real-secret",
                "X-Approved-By": "someone.else",
            },
            json={"family": "insurance", "columns": {"Gross Premium": "gross_premium"}},
        )
        assert response.status_code == 201
        assert "someone.else" not in response.json()["confirmed_by"]


class TestProposalsAreValidatedBeforeStorage:
    def test_an_invented_canonical_field_is_rejected(self, writable_client: TestClient) -> None:
        """Model output is untrusted input. A field name outside the adapter's set never lands."""
        response = writable_client.post(
            "/mappings/Northgate/confirm",
            headers={"X-Approver-Token": "test-token-not-a-real-secret"},
            json={"family": "insurance", "columns": {"Gross Premium": "gross_premium_v2"}},
        )
        assert response.status_code == 422
        assert "gross_premium_v2" in response.json()["detail"]

    def test_an_unknown_family_is_rejected(self, writable_client: TestClient) -> None:
        response = writable_client.post(
            "/mappings/Northgate/confirm",
            headers={"X-Approver-Token": "test-token-not-a-real-secret"},
            json={"family": "shipping", "columns": {"A": "gross_premium"}},
        )
        assert response.status_code == 422

    def test_an_empty_mapping_is_rejected(self, writable_client: TestClient) -> None:
        response = writable_client.post(
            "/mappings/Northgate/confirm",
            headers={"X-Approver-Token": "test-token-not-a-real-secret"},
            json={"family": "insurance", "columns": {}},
        )
        assert response.status_code == 422


class TestSettings:
    def test_read_only_defaults_to_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A deployment that configured nothing serves the console and cannot be written to."""
        monkeypatch.delenv("BX_READ_ONLY", raising=False)
        monkeypatch.delenv("BX_APPROVER_TOKEN", raising=False)
        get_settings.cache_clear()
        config: Settings = get_settings()
        assert config.read_only is True
        assert config.approver_token is None
        assert config.writes_enabled is False
        get_settings.cache_clear()

    def test_a_typo_in_a_boolean_is_an_error_not_a_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BX_READ_ONLY", "flase")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="not a boolean"):
            get_settings()
        get_settings.cache_clear()
