"""Operator authentication: bearer tokens for the API, a session cookie for the dashboard.

Until Unit 8 the application enforced nothing and the deployment relied on a
basic-auth gate at the Caddy proxy (docs/deployment.md §7). This module moves
the control into the application, so a proxy misconfiguration — or a second
route to port 8000 — no longer exposes the compliance record. The proxy gate
stays as defence in depth.

Configuration is one environment variable, ``AQUAOPS_OPERATOR_TOKENS``: a
comma-separated list of ``operator_id=token`` entries. The token may be given in
clear or as ``sha256:<hex digest>``, so the secret itself never has to sit in a
unit file. Only SHA-256 digests are kept in memory and compared, always with
``hmac.compare_digest``; no token is ever logged.

When the variable is unset the application behaves exactly as before — open —
and logs a warning at startup. That keeps the test suite, the demo seed and the
loopback workflows working, while making an unauthenticated production start
visible in the log rather than silent.

Who may reach what, when tokens are configured:

* ``/health`` and ``/static/*`` — open. The container HEALTHCHECK has no
  credentials, and the only static asset is the public htmx bundle.
* ``/login`` and ``/logout`` — open, so an operator can always start or end a
  session.
* ``/api/v1/*`` — ``Authorization: Bearer <token>`` only. A machine client has
  no reason to hold a browser cookie, and refusing the cookie here keeps the API
  from being driven by a page the operator happens to have open.
* everything else (the dashboard, its HTMX fragments, ``/docs``,
  ``/openapi.json``) — the session cookie or a bearer token. The API
  documentation describes the attack surface, so it is not public; an operator
  who is signed in to the dashboard can still read it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("aquaops.auth")

TOKENS_ENV = "AQUAOPS_OPERATOR_TOKENS"

#: Declared in docs/deployment.md since Unit 6 as the single-operator token. It
#: is still honoured, as the operator ``operator``, so an installation that
#: already set it does not silently become open.
LEGACY_TOKEN_ENV = "AQUAOPS_AUTH_TOKEN"
LEGACY_OPERATOR_ID = "operator"

SESSION_COOKIE = "aquaops_session"

#: One shift. Long enough that an operator is not signed out mid-round, short
#: enough that a session left open on a shared municipal laptop does not last
#: into the next day.
SESSION_TTL_SECONDS = 12 * 60 * 60

#: ``secrets.token_urlsafe(32)`` yields 43 characters; anything far shorter is
#: a password someone typed, and a guessable bearer token is no control at all.
MIN_TOKEN_LENGTH = 20

_OPERATOR_ID = re.compile(r"^[A-Za-z0-9_.@-]{1,40}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

#: The request-scope key under which the middleware records who is signed in.
SCOPE_KEY = "aquaops.operator"

PUBLIC_PATHS = frozenset({"/health", "/login", "/logout"})
PUBLIC_PREFIXES = ("/static/",)
API_PREFIX = "/api/"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OperatorTokens:
    """The configured operators, keyed by the SHA-256 digest of their token."""

    digests: Mapping[str, str] = field(default_factory=dict)  # operator id -> hex digest

    @property
    def enabled(self) -> bool:
        return bool(self.digests)

    def identify(self, token: str) -> str | None:
        """The operator a presented token belongs to, or ``None``.

        Every entry is compared, even after a match, so the time taken does not
        reveal which entry (or how many) the token was checked against.
        """
        presented = token_digest(token)
        found: str | None = None
        for operator_id, digest in self.digests.items():
            if hmac.compare_digest(presented, digest):
                found = operator_id
        return found


def parse_tokens(raw: str) -> dict[str, str]:
    """Parse ``AQUAOPS_OPERATOR_TOKENS``. Raises ``ValueError`` on a bad entry.

    A malformed variable stops the application from starting. Ignoring a bad
    entry would leave that operator locked out with no explanation, and ignoring
    the whole variable would leave the system open.
    """
    digests: dict[str, str] = {}
    for position, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        operator_id, sep, secret = entry.partition("=")
        operator_id, secret = operator_id.strip(), secret.strip()
        if not sep or not secret:
            raise ValueError(f"{TOKENS_ENV} entry {position} is not operator_id=token")
        if not _OPERATOR_ID.match(operator_id):
            raise ValueError(
                f"{TOKENS_ENV} entry {position}: operator id must be 1-40 characters "
                "from A-Z a-z 0-9 _ . @ -"
            )
        if operator_id in digests:
            raise ValueError(f"{TOKENS_ENV} names operator {operator_id!r} twice")
        if secret.startswith("sha256:"):
            digest = secret.removeprefix("sha256:").lower()
            if not _SHA256_HEX.match(digest):
                raise ValueError(
                    f"{TOKENS_ENV} entry {position}: sha256: must be followed by 64 hex digits"
                )
        else:
            if len(secret) < MIN_TOKEN_LENGTH:
                raise ValueError(
                    f"{TOKENS_ENV} entry {position}: token is shorter than "
                    f"{MIN_TOKEN_LENGTH} characters; generate one with secrets.token_urlsafe(32)"
                )
            digest = token_digest(secret)
        digests[operator_id] = digest
    return digests


def load_tokens(environ: Mapping[str, str] | None = None) -> OperatorTokens:
    env = os.environ if environ is None else environ
    digests = parse_tokens(env.get(TOKENS_ENV, ""))
    legacy = env.get(LEGACY_TOKEN_ENV, "").strip()
    if legacy and LEGACY_OPERATOR_ID not in digests:
        digests.update(parse_tokens(f"{LEGACY_OPERATOR_ID}={legacy}"))
    return OperatorTokens(digests)


# --------------------------------------------------------------- sessions
#
# The cookie is ``operator_id:expiry:signature``. The signature is an HMAC keyed
# with that operator's token digest, which has two useful consequences without a
# separate secret to manage: a cookie cannot be forged without the token, and
# rotating an operator's token ends every session that token opened.


def _session_mac(digest: str, operator_id: str, expires: int) -> str:
    message = f"{operator_id}:{expires}".encode()
    return hmac.new(bytes.fromhex(digest), message, hashlib.sha256).hexdigest()


def sign_session(tokens: OperatorTokens, operator_id: str, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + SESSION_TTL_SECONDS)
    mac = _session_mac(tokens.digests[operator_id], operator_id, expires)
    return f"{operator_id}:{expires}:{mac}"


def read_session(tokens: OperatorTokens, value: str, now: float | None = None) -> str | None:
    """The operator a session cookie proves, or ``None`` if it is invalid or expired."""
    operator_id, _, rest = value.partition(":")
    expires_text, _, mac = rest.partition(":")
    digest = tokens.digests.get(operator_id)
    if digest is None or not expires_text.isdigit():
        return None
    expires = int(expires_text)
    if not hmac.compare_digest(mac, _session_mac(digest, operator_id, expires)):
        return None
    if expires < (time.time() if now is None else now):
        return None
    return operator_id


def bearer_token(headers: Mapping[str, str]) -> str | None:
    scheme, _, credentials = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


def current_operator(request: Request) -> str | None:
    """The authenticated operator for this request; ``None`` when auth is off."""
    operator: str | None = request.scope.get(SCOPE_KEY)
    return operator


# ------------------------------------------------------------- middleware


class OperatorAuthMiddleware:
    """Deny by default: every path not listed as public needs a credential."""

    def __init__(self, app: ASGIApp, tokens: OperatorTokens) -> None:
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.tokens.enabled:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        presented = bearer_token(request.headers)
        operator = self.tokens.identify(presented) if presented else None
        if operator is None and not path.startswith(API_PREFIX):
            cookie = request.cookies.get(SESSION_COOKIE)
            operator = read_session(self.tokens, cookie) if cookie else None

        if operator is not None:
            scope[SCOPE_KEY] = operator
            await self.app(scope, receive, send)
            return

        if presented:
            logger.warning("rejected an invalid bearer token for %s %s", request.method, path)
        response = self._refusal(request, path, bad_token=presented is not None)
        await response(scope, receive, send)

    @staticmethod
    def _refusal(request: Request, path: str, *, bad_token: bool) -> Response:
        challenge = 'Bearer realm="aquaops"'
        if bad_token:
            challenge += ', error="invalid_token"'
        headers = {"WWW-Authenticate": challenge}
        if path.startswith(API_PREFIX) or path == "/openapi.json":
            return JSONResponse(
                {"detail": "operator token required"}, status_code=401, headers=headers
            )
        if request.headers.get("HX-Request") == "true":
            # A swap into a dashboard panel cannot show a login form sensibly;
            # HX-Redirect makes htmx send the whole tab to the sign-in page.
            headers["HX-Redirect"] = "/login"
            return Response("sign-in required", status_code=401, headers=headers)
        target = "/login"
        if request.method in ("GET", "HEAD") and path != "/":
            target += "?next=" + quote(request.url.path + _query(request), safe="")
        return RedirectResponse(target, status_code=303)


def _query(request: Request) -> str:
    return f"?{request.url.query}" if request.url.query else ""


# ------------------------------------------------------- sign in, sign out

router = APIRouter(include_in_schema=False)


def _tokens(request: Request) -> OperatorTokens:
    tokens: OperatorTokens = request.app.state.operator_tokens
    return tokens


def _safe_next(target: str) -> str:
    """Only same-site paths, so the login form cannot be used as an open redirect."""
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


@router.get("/login", response_class=Response)
def login_form(request: Request, next_path: str = Query(default="/", alias="next")) -> Response:
    from modules.m5_dashboard import render_login

    if not _tokens(request).enabled:
        return RedirectResponse("/", status_code=303)
    return Response(render_login(next_path=_safe_next(next_path)), media_type="text/html")


@router.post("/login", response_class=Response)
def login(
    request: Request,
    token: str = Form(default=""),
    next_path: str = Form(default="/", alias="next"),
) -> Response:
    from modules.m5_dashboard import render_login

    tokens = _tokens(request)
    if not tokens.enabled:
        return RedirectResponse("/", status_code=303)
    operator = tokens.identify(token.strip()) if token.strip() else None
    if operator is None:
        client = request.client.host if request.client else "unknown"
        logger.warning("rejected a dashboard sign-in from %s", client)
        html = render_login(next_path=_safe_next(next_path), error="That token was not recognised.")
        return Response(html, status_code=401, media_type="text/html")

    logger.info("operator %s signed in", operator)
    response = RedirectResponse(_safe_next(next_path), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(tokens, operator),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        # Behind Caddy the scheme comes from X-Forwarded-Proto, which uvicorn
        # trusts from 127.0.0.1 by default; on plain-HTTP loopback the cookie
        # must not be Secure or the browser would never send it back.
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return response


@router.post("/logout", response_class=Response)
def logout(request: Request) -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
    )
    return response

