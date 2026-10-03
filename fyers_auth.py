"""
Fyers API v3 login.

I haven't been able to verify this against a real Fyers account (no
credentials, no network access from where I write this) -- it's built
directly from Fyers' official SDK documentation and usage examples, not
guessed. Treat the first run as a test, same as kite_auth.py originally was.

This is a MANUAL login (you open a link, log in, paste back a code) rather
than automated like the Kite module. Reason: for Kite I found a well-
documented community pattern for automating the login; for Fyers I haven't
verified one exists or still works, and I'd rather give you something that
definitely works manually than something that might silently fail trying
to be clever. Automating this is a separate follow-up if you want it, once
we've confirmed the manual flow works end to end.

Setup (one-time), before any of this will work:
1. Go to https://myapi.fyers.in/dashboard/ and create an app.
2. You'll get a `client_id` (looks like "ABCDE1234F-100") and a `secret_key`.
3. Set a redirect_uri -- for a Streamlit app, your app's own URL works.
4. pip install fyers-apiv3

Requires: pip install fyers-apiv3
"""
from urllib.parse import parse_qs, urlparse

from fyers_apiv3 import fyersModel


class FyersLoginError(RuntimeError):
    """Raised when the login/token exchange doesn't complete as expected."""


def get_login_url(client_id: str, secret_key: str, redirect_uri: str, state: str = "momentum_app") -> fyersModel.SessionModel:
    """Returns a configured SessionModel; call .generate_authcode() on it for
    the URL to send the user to."""
    return fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        state=state,
        grant_type="authorization_code",
    )


def extract_auth_code(redirected_url: str) -> str:
    """After logging in, Fyers sends the browser to your redirect_uri with
    '?s=ok&code=...&auth_code=...' (or similar) attached. Paste that whole
    URL here (or just the auth_code value itself -- both work)."""
    if "auth_code=" not in redirected_url and "code=" not in redirected_url:
        return redirected_url.strip()  # assume they pasted the bare code
    parsed = parse_qs(urlparse(redirected_url).query)
    code = (parsed.get("auth_code") or parsed.get("code") or [None])[0]
    if not code:
        raise FyersLoginError(
            "Couldn't find an auth code in that URL. Paste the FULL address bar "
            "contents right after logging in, including everything after the '?'."
        )
    return code


def exchange_code_for_token(client_id: str, secret_key: str, redirect_uri: str, auth_code: str) -> str:
    """Trades a one-time auth_code for an access_token. Call this right after
    extract_auth_code() -- the code is single-use and short-lived."""
    session = get_login_url(client_id, secret_key, redirect_uri)
    session.set_token(auth_code)
    response = session.generate_token()
    access_token = response.get("access_token")
    if not access_token:
        raise FyersLoginError(f"Token exchange failed: {response}")
    return access_token


def get_authenticated_fyers(client_id: str, access_token: str):
    """Returns a ready-to-use FyersModel instance, given a client_id and an
    access_token you already obtained via the manual flow above."""
    return fyersModel.FyersModel(client_id=client_id, token=access_token, is_async=False, log_path="")


# ---------------------------------------------------------------------------
# Suggested Streamlit flow (manual, once a day):
#   1. Button: "Get Fyers login link" -> show get_login_url(...).generate_authcode()
#   2. User clicks it, logs in, copies the resulting URL from their browser
#   3. Text box: paste that URL -> extract_auth_code() -> exchange_code_for_token()
#   4. Cache the resulting access_token for the rest of the day (st.cache_resource)
# ---------------------------------------------------------------------------
