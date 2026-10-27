"""Regression coverage for hosting the web app below a URL path prefix."""

from fastapi.testclient import TestClient

import webapp


def test_base_path_normalization():
    assert webapp._normalize_base_path("") == ""
    assert webapp._normalize_base_path("/") == ""
    assert webapp._normalize_base_path("home_evaluator/") == "/home_evaluator"


def test_prefixed_routes_and_browser_urls(monkeypatch):
    monkeypatch.setattr(webapp, "_BASE_PATH", "/home_evaluator")

    with TestClient(webapp.app) as client:
        health = client.get("/home_evaluator/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        page = client.get("/home_evaluator/")
        assert page.status_code == 200
        assert 'action="/home_evaluator/evaluate"' in page.text
        assert 'src="/home_evaluator/static/img/home-hero.jpg"' in page.text
        assert "fetch('/home_evaluator/api/geocode?" in page.text

        static_asset = client.get("/home_evaluator/static/plotly.min.js")
        assert static_asset.status_code == 200


def test_unprefixed_health_check_still_works(monkeypatch):
    monkeypatch.setattr(webapp, "_BASE_PATH", "/home_evaluator")

    with TestClient(webapp.app) as client:
        assert client.get("/healthz").status_code == 200


def test_budget_auth_check_allows_any_valid_google_user(monkeypatch):
    monkeypatch.setattr(webapp, "_OAUTH_ON", True)
    monkeypatch.setattr(webapp, "_ADMIN_EMAILS", {"owner@example.com"})

    with TestClient(webapp.app) as client:
        monkeypatch.setattr(webapp, "_current_user", lambda _request: None)
        assert client.get("/auth/budget-check").status_code == 401

        monkeypatch.setattr(
            webapp, "_current_user",
            lambda _request: {"id": 2, "email": "someone@example.com"},
        )
        signed_in = client.get("/auth/budget-check")
        assert signed_in.status_code == 204
        assert signed_in.headers["x-authenticated-email"] == "someone@example.com"
        assert signed_in.headers["x-authenticated-user-id"] == "2"
        assert "x-budget-owner" not in signed_in.headers

        monkeypatch.setattr(
            webapp, "_current_user",
            lambda _request: {"id": 0, "email": "invalid@example.com"},
        )
        assert client.get("/auth/budget-check").status_code == 403

        monkeypatch.setattr(
            webapp, "_current_user",
            lambda _request: {"id": 3, "email": ""},
        )
        assert client.get("/auth/budget-check").status_code == 403

        monkeypatch.setattr(
            webapp, "_current_user",
            lambda _request: {"id": 1, "email": "OWNER@example.com"},
        )
        allowed = client.get("/auth/budget-check")
        assert allowed.status_code == 204
        assert allowed.headers["x-authenticated-email"] == "owner@example.com"
        assert allowed.headers["x-authenticated-user-id"] == "1"
        assert allowed.headers["x-budget-owner"] == "1"


def test_oauth_return_paths_are_same_origin_only():
    assert webapp._safe_return_path("/budgeting_beaver/") == "/budgeting_beaver/"
    assert webapp._safe_return_path("https://attacker.example") is None
    assert webapp._safe_return_path("//attacker.example") is None
    assert webapp._safe_return_path("/safe\\redirect") is None
