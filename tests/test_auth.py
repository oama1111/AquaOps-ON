"""Operator authentication (`app/auth.py`): bearer tokens, the session cookie, and
the unset-means-open default.

The suite pins both halves of the contract. With ``AQUAOPS_OPERATOR_TOKENS``
unset the application must behave exactly as it did before authentication
existed, because the demo seed, the Unit 6 harness and every other test rely on
that. With it set, nothing but ``/health``, the static bundle and the sign-in
pages may answer without a credential.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    load_tokens,
    parse_tokens,
    read_session,
    sign_session,
    token_digest,
)
from app.db import configure

DEMO_CSV = Path(__file__).resolve().parents[1] / "examples" / "demo_lab_results.csv"
TOKEN = "op1-test-token-0123456789abcdef"
OTHER_TOKEN = "op2-test-token-fedcba9876543210"
HX = {"HX-Request": "true"}


def _bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(db_url: str, base_url: str = "http://testserver") -> TestClient:
    os.environ["AQUAOPS_DB_URL"] = db_url
    configure(db_url)
    from app.main import create_app

    return TestClient(create_app(), base_url=base_url)


@pytest.fixture()
def secured(db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An application with two operators; op-2 is configured by digest only."""
    monkeypatch.setenv(
        "AQUAOPS_OPERATOR_TOKENS", f"op-1={TOKEN}, op-2=sha256:{token_digest(OTHER_TOKEN)}"
    )
    with _client(db_url) as test_client:
        yield test_client


def _sign_in(client: TestClient, token: str = TOKEN, next_path: str = "/"):
    return client.post(
        "/login", data={"token": token, "next": next_path}, follow_redirects=False
    )


# ------------------------------------------------------------ unset = open


def test_unset_leaves_api_and_dashboard_open(client: TestClient) -> None:
    assert client.get("/api/v1/alerts").status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert "Sign out" not in page.text
    assert client.get("/openapi.json").status_code == 200


def test_unset_logs_a_startup_warning(db_url: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="aquaops.auth"):
        with _client(db_url):
            pass
    assert "AQUAOPS_OPERATOR_TOKENS is not set" in caplog.text


def test_login_page_redirects_home_when_auth_is_off(client: TestClient) -> None:
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


# ------------------------------------------------------------------- API


def test_api_without_token_is_401_with_challenge(secured: TestClient) -> None:
    response = secured.get("/api/v1/alerts")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert response.json() == {"detail": "operator token required"}


def test_api_with_wrong_token_is_401(secured: TestClient) -> None:
    response = secured.get("/api/v1/alerts", headers=_bearer("not-the-token-at-all-xyz"))
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_api_rejects_a_non_bearer_scheme(secured: TestClient) -> None:
    response = secured.get("/api/v1/alerts", headers={"Authorization": f"Basic {TOKEN}"})
    assert response.status_code == 401


def test_api_with_correct_bearer_is_200(secured: TestClient) -> None:
    assert secured.get("/api/v1/alerts", headers=_bearer()).status_code == 200
    assert secured.get("/api/v1/alerts", headers=_bearer(OTHER_TOKEN)).status_code == 200


def test_api_does_not_accept_the_session_cookie(secured: TestClient) -> None:
    _sign_in(secured)
    assert secured.get("/api/v1/alerts").status_code == 401


def test_health_and_static_stay_open(secured: TestClient) -> None:
    assert secured.get("/health").status_code == 200
    assert secured.get("/static/htmx.min.js").status_code == 200


def test_api_documentation_needs_a_credential(secured: TestClient) -> None:
    assert secured.get("/openapi.json").status_code == 401
    assert secured.get("/docs", follow_redirects=False).status_code == 303
    spec = secured.get("/openapi.json", headers=_bearer())
    assert spec.status_code == 200
    assert "HTTPBearer" in spec.json()["components"]["securitySchemes"]
    _sign_in(secured)
    assert secured.get("/docs").status_code == 200


# -------------------------------------------------------------- dashboard


def test_dashboard_redirects_to_login_without_a_session(secured: TestClient) -> None:
    response = secured.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    deep = secured.get("/dash/week?week=2026-W38", follow_redirects=False)
    assert deep.headers["location"] == "/login?next=%2Fdash%2Fweek%3Fweek%3D2026-W38"
    assert "Operator token" in secured.get("/login").text


def test_unauthenticated_htmx_request_gets_401_and_hx_redirect(secured: TestClient) -> None:
    response = secured.get("/dash/alerts", headers=HX)
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


def test_login_with_wrong_token_sets_no_cookie(secured: TestClient) -> None:
    response = _sign_in(secured, token="wrong-token-wrong-token")
    assert response.status_code == 401
    assert "not recognised" in response.text
    assert SESSION_COOKIE not in response.cookies


def test_login_sets_a_strict_httponly_cookie_and_dashboard_loads(secured: TestClient) -> None:
    response = _sign_in(secured)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Secure" not in set_cookie  # plain-HTTP loopback: Secure would never return

    page = secured.get("/")
    assert page.status_code == 200
    assert "Signed in as <strong>op-1</strong>" in page.text
    fragment = secured.get("/dash/alerts", headers=HX)
    assert fragment.status_code == 200
    assert "Alert inbox" in fragment.text


def test_cookie_is_secure_over_https(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AQUAOPS_OPERATOR_TOKENS", f"op-1={TOKEN}")
    with _client(db_url, base_url="https://testserver") as https_client:
        response = _sign_in(https_client)
    assert "Secure" in response.headers["set-cookie"]


def test_login_returns_to_the_requested_page_but_never_off_site(secured: TestClient) -> None:
    assert _sign_in(secured, next_path="/dash/week").headers["location"] == "/dash/week"
    assert _sign_in(secured, next_path="//evil.example/").headers["location"] == "/"
    assert _sign_in(secured, next_path="https://evil.example/").headers["location"] == "/"


def test_logout_clears_the_session(secured: TestClient) -> None:
    _sign_in(secured)
    assert secured.get("/", follow_redirects=False).status_code == 200
    response = secured.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert f'{SESSION_COOKIE}=""' in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert secured.get("/", follow_redirects=False).status_code == 303


def test_forged_cookie_is_refused(secured: TestClient) -> None:
    secured.cookies.set(SESSION_COOKIE, "op-1:9999999999:" + "0" * 64)
    assert secured.get("/", follow_redirects=False).status_code == 303


# ---------------------------------------------------- audit identity


def _raise_sp15_alert(client: TestClient) -> str:
    client.post(
        "/dash/upload",
        files={"file": ("demo.csv", DEMO_CSV.read_bytes(), "text/csv")},
        headers={**HX, **_bearer()},
    )
    client.post("/api/v1/detect/run", params={"point": "SP15"}, headers=_bearer())
    alerts = client.get("/api/v1/alerts", headers=_bearer()).json()
    assert alerts
    return str(alerts[0]["id"])


def test_dashboard_ack_records_the_signed_in_operator(secured: TestClient) -> None:
    alert_id = _raise_sp15_alert(secured)
    _sign_in(secured, token=OTHER_TOKEN)
    inbox = secured.get("/dash/alerts", headers=HX).text
    assert 'value="op-2" readonly' in inbox
    # A typed name is ignored: the session says who acknowledged.
    secured.post(f"/dash/alerts/{alert_id}/ack", data={"operator_id": "someone-else"}, headers=HX)
    still_open = secured.get("/api/v1/alerts", headers=_bearer()).json()
    assert alert_id not in {row["id"] for row in still_open}
    from app.db import session_scope
    from app.models import AnomalyAlert

    with session_scope() as session:
        assert session.get(AnomalyAlert, alert_id).acknowledged_by == "op-2"


def test_api_ack_records_the_token_holder(secured: TestClient) -> None:
    alert_id = _raise_sp15_alert(secured)
    response = secured.post(
        f"/api/v1/alerts/{alert_id}/ack",
        json={"operator_id": "claimed-name", "note": "flushed"},
        headers=_bearer(),
    )
    assert response.status_code == 200
    assert response.json()["acknowledged_by"] == "op-1"


# ---------------------------------------------------- configuration parsing


def test_tokens_are_kept_only_as_digests() -> None:
    tokens = load_tokens({"AQUAOPS_OPERATOR_TOKENS": f"op-1={TOKEN}"})
    assert tokens.digests == {"op-1": token_digest(TOKEN)}
    assert TOKEN not in repr(tokens)
    assert tokens.identify(TOKEN) == "op-1"
    assert tokens.identify("nope") is None


def test_legacy_single_token_variable_is_still_honoured() -> None:
    tokens = load_tokens({"AQUAOPS_AUTH_TOKEN": TOKEN})
    assert tokens.enabled
    assert tokens.identify(TOKEN) == "operator"


@pytest.mark.parametrize(
    "raw",
    [
        "op-1",  # no token
        "op-1=short",  # guessable
        "bad id=" + TOKEN,  # space in the operator id
        f"op-1={TOKEN},op-1={OTHER_TOKEN}",  # duplicate operator
        "op-1=sha256:abc",  # truncated digest
    ],
)
def test_malformed_configuration_fails_loudly(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_tokens(raw)


def test_session_signature_and_expiry() -> None:
    tokens = load_tokens({"AQUAOPS_OPERATOR_TOKENS": f"op-1={TOKEN}"})
    cookie = sign_session(tokens, "op-1", now=1_000)
    assert read_session(tokens, cookie, now=1_000) == "op-1"
    assert read_session(tokens, cookie, now=1_000 + SESSION_TTL_SECONDS + 1) is None
    assert read_session(tokens, cookie.replace("op-1", "op-2", 1), now=1_000) is None
    assert read_session(tokens, "garbage", now=1_000) is None
    # Rotating the token ends the sessions it opened.
    rotated = load_tokens({"AQUAOPS_OPERATOR_TOKENS": f"op-1={OTHER_TOKEN}"})
    assert read_session(rotated, cookie, now=1_000) is None
