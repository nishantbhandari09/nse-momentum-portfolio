"""FYERS API v3 OAuth helpers for a Streamlit app.

The user only clicks the FYERS login link. The redirect returns an auth_code to
Streamlit, and the app exchanges it automatically; the user never copies/pastes it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from fyers_apiv3 import fyersModel

AUTH_BASE = "https://api-t1.fyers.in/api/v3"


class FyersLoginError(RuntimeError):
    """Raised when FYERS authentication does not complete."""


def get_login_url(
    client_id: str,
    secret_key: str,
    redirect_uri: str,
    state: str = "momentum_app",
) -> fyersModel.SessionModel:
    """Return a configured official SDK SessionModel for the login URL."""
    return fyersModel.SessionModel(
        client_id=client_id.strip(),
        secret_key=secret_key.strip(),
        redirect_uri=redirect_uri.strip(),
        response_type="code",
        state=state,
        grant_type="authorization_code",
    )


def create_signed_state(secret_key: str) -> str:
    """Create a self-contained, time-limited OAuth state value.

    It does not rely on Streamlit session_state surviving the round-trip through
    the external FYERS login page.
    """
    issued = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    payload = f"v1.{issued}.{nonce}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def validate_signed_state(state: str, secret_key: str, max_age_seconds: int = 1800) -> bool:
    """Validate the signed OAuth state and reject expired or tampered callbacks."""
    try:
        version, issued_text, nonce, signature = str(state).split(".", 3)
        if version != "v1" or not nonce:
            return False
        issued = int(issued_text)
        age = int(time.time()) - issued
        if age < -60 or age > max_age_seconds:
            return False
        payload = f"{version}.{issued_text}.{nonce}"
        expected = hmac.new(
            secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def extract_auth_code(redirected_url: str) -> str:
    """Read the code from a callback URL or accept a raw code for compatibility."""
    value = str(redirected_url or "").strip()
    if "auth_code=" not in value and "code=" not in value:
        return value
    parsed = parse_qs(urlparse(value).query)
    code = (parsed.get("auth_code") or parsed.get("code") or [None])[0]
    if not code:
        raise FyersLoginError("FYERS redirected back without an auth_code.")
    return str(code).strip()


def exchange_code_for_token(
    client_id: str,
    secret_key: str,
    redirect_uri: str,
    auth_code: str,
) -> str:
    """Exchange the one-time OAuth code using FYERS' documented v3 REST contract.

    This intentionally avoids SDK response.json() assumptions so empty/non-JSON
    broker responses become useful error messages instead of JSONDecodeErrors.
    """
    client_id = str(client_id).strip()
    secret_key = str(secret_key).strip()
    redirect_uri = str(redirect_uri).strip()
    auth_code = extract_auth_code(auth_code)

    if not client_id or not secret_key or not redirect_uri:
        raise FyersLoginError("FYERS_APP_ID, FYERS_SECRET_ID and FYERS_REDIRECT_URI are required.")
    if not auth_code:
        raise FyersLoginError("FYERS did not return an authorization code.")

    app_hash = hashlib.sha256(f"{client_id}:{secret_key}".encode("utf-8")).hexdigest()
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": app_hash,
        "code": auth_code,
    }
    try:
        response = requests.post(
            f"{AUTH_BASE}/validate-authcode",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise FyersLoginError(f"Could not reach FYERS token endpoint: {exc}") from exc

    body = (response.text or "").strip()
    try:
        data: dict[str, Any] = response.json() if body else {}
    except ValueError:
        data = {}

    if response.status_code >= 400 or not data.get("access_token"):
        detail = body[:1200] if body else "empty response body"
        raise FyersLoginError(
            f"FYERS token exchange failed (HTTP {response.status_code}). "
            f"Response: {detail}. Verify that the App ID and Secret belong to the same "
            "activated FYERS app, the redirect URI matches exactly, and this is a fresh login."
        )
    return str(data["access_token"])


def get_authenticated_fyers(client_id: str, access_token: str):
    """Return the FYERS v3 client authenticated with the current access token."""
    return fyersModel.FyersModel(
        client_id=client_id.strip(),
        token=access_token,
        is_async=False,
        log_path="",
    )
