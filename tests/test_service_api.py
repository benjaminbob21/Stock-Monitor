"""Service + FastAPI endpoint tests (network-free via fake providers)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from stock_monitor.api.app import AppState, app, get_state


def _state(world: SimpleNamespace, model_loaded: bool = True) -> AppState:
    return AppState(
        model=world.model if model_loaded else None,
        model_version=world.version if model_loaded else None,
        price_provider=world.price_provider,
        fundamental_provider=world.fundamental_provider,
        db_path=None,
        label_window_months=12,
    )


def _client(state: AppState) -> TestClient:
    app.dependency_overrides[get_state] = lambda: state
    return TestClient(app)


def test_health_reports_model_loaded(world: SimpleNamespace) -> None:
    try:
        client = _client(_state(world))
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["model_loaded"] is True
        assert body["model_version"] == world.version
    finally:
        app.dependency_overrides.clear()


def test_score_returns_explained_payload(world: SimpleNamespace) -> None:
    try:
        client = _client(_state(world))
        resp = client.get(f"/score/{world.ticker}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ticker"] == world.ticker
        assert 0 <= body["conviction"] <= 100
        assert 1 <= len(body["drivers"]) <= 3
        assert body["calibrated"] is False
        assert "disclaimer" in body
        assert isinstance(body["risk_flags"], list)
    finally:
        app.dependency_overrides.clear()


def test_score_unknown_ticker_returns_404(world: SimpleNamespace) -> None:
    try:
        client = _client(_state(world))
        assert client.get("/score/ZZZ").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_score_without_model_returns_503(world: SimpleNamespace) -> None:
    try:
        client = _client(_state(world, model_loaded=False))
        assert client.get(f"/score/{world.ticker}").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_api_key_enforced_when_secret_set(
    world: SimpleNamespace, monkeypatch
) -> None:
    import sys

    app_module = sys.modules["stock_monitor.api.app"]

    monkeypatch.setattr(
        app_module,
        "get_settings",
        lambda: SimpleNamespace(api_shared_secret="s3cret", run_scheduler=False),
    )
    try:
        client = _client(_state(world))
        # /health stays open so uptime checks work without the key.
        assert client.get("/health").status_code == 200
        # Protected route rejects missing / wrong keys, accepts the right one.
        assert client.get(f"/score/{world.ticker}").status_code == 401
        assert (
            client.get(
                f"/score/{world.ticker}", headers={"x-api-key": "nope"}
            ).status_code
            == 401
        )
        ok = client.get(f"/score/{world.ticker}", headers={"x-api-key": "s3cret"})
        assert ok.status_code == 200, ok.text
    finally:
        app.dependency_overrides.clear()

