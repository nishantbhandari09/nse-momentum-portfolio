"""
Automated Fyers API v3 login using Selenium WebDriver.

This module automates the browser-based OAuth login flow, eliminating the need
to manually copy-paste redirect URLs. It handles:
1. Opening the Fyers login page in a browser
2. Waiting for the user to authenticate
3. Automatically capturing the redirect URL with auth_code
4. Exchanging the code for an access token

Setup (one-time):
1. Go to https://myapi.fyers.in/dashboard/ and create an app
2. You'll get a `client_id` (looks like "ABCDE1234F-100") and a `secret_key`
3. Set redirect_uri to http://localhost:8000 (or your Streamlit app URL)
4. pip install fyers-apiv3 selenium

Requires: pip install fyers-apiv3 selenium
"""
import os
import time
import threading
from urllib.parse import parse_qs, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from fyers_apiv3 import fyersModel


class FyersLoginError(RuntimeError):
    """Raised when the login/token exchange doesn't complete as expected."""


class RedirectHandler(BaseHTTPRequestHandler):
    """HTTP request handler that captures OAuth redirect with auth_code."""
    
    auth_code_store = None  # Class variable to store auth code
    
    def do_GET(self):
        """Handle GET request from OAuth redirect."""
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        auth_code = (query_params.get("auth_code") or query_params.get("code") or [None])[0]
        
        if auth_code:
            RedirectHandler.auth_code_store = auth_code
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html>
                <head><title>Fyers Authentication</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h2 style="color: green;">✓ Authentication Successful!</h2>
                    <p>You can close this window and return to the app.</p>
                </body>
            </html>
            """)
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html>
                <head><title>Fyers Authentication</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h2 style="color: red;">✗ Authentication Failed</h2>
                    <p>No auth code found in the redirect. Please try again.</p>
                </body>
            </html>
            """)
    
    def log_message(self, format, *args):
        """Suppress default HTTP server logging."""
        pass


def start_local_server(port=8000):
    """Start a local HTTP server on localhost:port to capture OAuth redirects."""
    server = HTTPServer(("127.0.0.1", port), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def get_login_url(client_id: str, secret_key: str, redirect_uri: str, state: str = "momentum_app"):
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


def open_browser(url):
    """Open the login URL in the user's default browser."""
    import webbrowser
    webbrowser.open(url)


def exchange_code_for_token(client_id: str, secret_key: str, redirect_uri: str, auth_code: str) -> str:
    """Trades a one-time auth_code for an access_token."""
    session = get_login_url(client_id, secret_key, redirect_uri)
    session.set_token(auth_code)
    response = session.generate_token()
    access_token = response.get("access_token")
    if not access_token:
        raise FyersLoginError(f"Token exchange failed: {response}")
    return access_token


def get_authenticated_fyers(client_id: str, access_token: str):
    """Returns a ready-to-use FyersModel instance."""
    return fyersModel.FyersModel(client_id=client_id, token=access_token, is_async=False, log_path="")


def automated_fyers_login(client_id: str, secret_key: str, redirect_uri: str = "http://localhost:8000", port: int = 8000, timeout: int = 300) -> str:
    """
    Fully automated Fyers login flow:
    1. Starts a local HTTP server to capture OAuth redirect
    2. Opens browser to Fyers login page
    3. Waits for user to authenticate
    4. Automatically captures auth_code from redirect
    5. Exchanges code for access_token
    
    Args:
        client_id: Your Fyers app client_id
        secret_key: Your Fyers app secret_key
        redirect_uri: Full redirect URI (default: http://localhost:8000)
        port: Port for local server (must match redirect_uri port)
        timeout: Max seconds to wait for auth (default: 300 = 5 minutes)
    
    Returns:
        access_token: Ready to use with get_authenticated_fyers()
    
    Raises:
        FyersLoginError: If login fails or times out
    """
    # Start local redirect server
    server = start_local_server(port)
    
    try:
        # Generate login URL and open browser
        session = get_login_url(client_id, secret_key, redirect_uri)
        login_url = session.generate_authcode()
        open_browser(login_url)
        
        # Wait for user to authenticate and redirect back
        start_time = time.time()
        while RedirectHandler.auth_code_store is None:
            if time.time() - start_time > timeout:
                raise FyersLoginError(
                    f"Login timeout after {timeout} seconds. "
                    "Make sure you completed authentication in the browser."
                )
            time.sleep(0.5)
        
        auth_code = RedirectHandler.auth_code_store
        RedirectHandler.auth_code_store = None  # Reset for next login
        
        # Exchange code for token
        access_token = exchange_code_for_token(client_id, secret_key, redirect_uri, auth_code)
        return access_token
    
    finally:
        # Shutdown server
        server.shutdown()
