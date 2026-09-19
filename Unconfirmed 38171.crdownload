"""
Automated (unattended) Zerodha Kite Connect login.

READ THIS FIRST.

Kite Connect's documented login flow (kite.trade/docs/connect/v3/user/#login-flow)
is designed to be done by a human, in a browser, once a day. The function
below instead replays that flow in code, against Zerodha's internal
endpoints (the same ones the Kite login *page* itself calls) rather than the
public, documented Kite Connect API.

Two things to know before you use it:

1. Zerodha's own forum moderators have called this kind of login automation
   "against exchange regulations" and warned it can break at any time with
   no notice -- a changed endpoint, an added CAPTCHA, etc. -- precisely
   because it isn't a supported, public contract. In practice this is very
   commonly done by retail algo traders in India, but it is genuinely
   against what Zerodha wants people doing, not just "unofficial" in a
   trivial sense. (Source thread: kite.trade/forum/discussion/13538)
2. It means your Zerodha login password and TOTP secret have to live
   somewhere your code can read them -- not just in your head. Anyone who
   gets that file/secret store can log into your trading account. That's a
   real risk to weigh, not a formality.

Neither of those is a reason this can't be done -- it's your account and
your call -- just going in with clear eyes. The supported alternative is a
60-second manual login once a day (happy to wire that up instead, or as a
fallback inside the same app -- see the note at the bottom of this file).

Requires: pip install pyotp kiteconnect requests
"""
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from kiteconnect import KiteConnect


class AutoLoginError(RuntimeError):
    """Raised when any step of the automated login fails -- most often
    because Zerodha showed a CAPTCHA or an extra verification step instead
    of completing a plain login. When this happens, there is no code fix;
    it means a real human has to log in through the browser that one time."""


def automated_login(api_key: str, api_secret: str, user_id: str,
                     password: str, totp_secret: str, timeout: int = 15) -> str:
    """
    Logs into Zerodha end-to-end and returns a ready-to-use access_token.

    totp_secret is NOT your 6-digit code -- it's the fixed secret key behind
    it (the text string shown next to the QR code the one time you set up
    TOTP in Kite's settings). If you never saved that string, you'll need to
    reset/reconfigure TOTP in Kite to see it again; Zerodha only shows it once.
    """
    session = requests.Session()

    login_page = session.get(
        "https://kite.trade/connect/login",
        params={"v": 3, "api_key": api_key},
        timeout=timeout,
    )

    step1 = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": user_id, "password": password},
        timeout=timeout,
    ).json()
    if step1.get("status") != "success":
        raise AutoLoginError(f"Username/password step failed: {step1}")

    request_id = step1["data"]["request_id"]
    twofa_type = step1["data"].get("twofa_type", "totp")
    totp_code = pyotp.TOTP(totp_secret).now()

    step2 = session.post(
        "https://kite.zerodha.com/api/twofa",
        data={"user_id": user_id, "request_id": request_id,
              "twofa_value": totp_code, "twofa_type": twofa_type},
        timeout=timeout,
    ).json()
    if step2.get("status") != "success":
        raise AutoLoginError(f"2FA step failed: {step2}")

    final = session.get(
        login_page.url + "&skip_session=true",
        allow_redirects=True,
        timeout=timeout,
    )
    token_values = parse_qs(urlparse(final.url).query).get("request_token")
    if not token_values:
        raise AutoLoginError(
            "No request_token came back -- Zerodha most likely served a "
            "CAPTCHA or extra check instead of finishing the login. This is "
            "the failure mode mentioned above: it needs a human login that "
            "one time, there's no code-only retry that fixes it."
        )

    kite = KiteConnect(api_key=api_key)
    session_data = kite.generate_session(token_values[0], api_secret=api_secret)
    return session_data["access_token"]


def get_authenticated_kite(api_key: str, api_secret: str, user_id: str,
                            password: str, totp_secret: str) -> KiteConnect:
    """Convenience wrapper: returns a KiteConnect instance that's already
    logged in and ready to call e.g. .historical_data(...) / .ltp(...)."""
    access_token = automated_login(api_key, api_secret, user_id, password, totp_secret)
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


# ---------------------------------------------------------------------------
# Resilience note: consider wrapping calls to automated_login() in a
# try/except AutoLoginError, and on failure falling back to asking for a
# request_token by hand that one day (e.g. a text box in your Streamlit app)
# rather than letting the whole app crash silently when Zerodha shows a
# CAPTCHA. That keeps you working even on the days automation doesn't.
# ---------------------------------------------------------------------------
