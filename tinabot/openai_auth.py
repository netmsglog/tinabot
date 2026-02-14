"""OpenAI OAuth PKCE authentication (ChatGPT login).

Implements the same auth flow as `codex login` in the OpenAI Codex CLI:
1. Generate PKCE pair (verifier + S256 challenge)
2. Open browser to OpenAI auth page
3. Local callback server captures authorization code
4. Exchange code for access + refresh tokens
5. Auto-refresh before expiry

Tokens authenticate against the ChatGPT backend API (Responses API format),
NOT the standard Chat Completions API at api.openai.com/v1.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from loguru import logger

# OAuth constants (from Codex CLI source)
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"
CALLBACK_PORT = 1455
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
AUDIENCE = "https://api.openai.com/v1"

TOKEN_FILE = Path.home() / ".tinabot" / "openai_auth.json"

# Refresh 5 minutes before expiry
REFRESH_MARGIN_SECONDS = 300


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and S256 challenge."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    challenge_digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(challenge_digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without verification (we trust the issuer)."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    # Add padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    try:
        data = base64.urlsafe_b64decode(payload)
        return json.loads(data)
    except Exception:
        return {}


def _extract_account_id(access_token: str) -> str | None:
    """Extract chatgpt_account_id from JWT claims."""
    claims = _decode_jwt_payload(access_token)
    return claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")


def _open_browser(url: str):
    """Open URL in the default browser."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            os.startfile(url)
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.warning(f"Could not open browser: {e}")


class OpenAIAuth:
    """Manages OpenAI OAuth tokens for ChatGPT backend API access."""

    def __init__(self):
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self._account_id: str | None = None
        self._load()

    def _load(self):
        """Load tokens from disk."""
        if not TOKEN_FILE.exists():
            return
        try:
            data = json.loads(TOKEN_FILE.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._expires_at = data.get("expires_at", 0.0)
            self._account_id = data.get("account_id")
        except Exception as e:
            logger.warning(f"Failed to load OAuth tokens: {e}")

    def _save(self):
        """Persist tokens to disk."""
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
            "account_id": self._account_id,
        }
        TOKEN_FILE.write_text(json.dumps(data, indent=2) + "\n")
        # Restrict permissions
        TOKEN_FILE.chmod(0o600)

    @property
    def is_logged_in(self) -> bool:
        return self._access_token is not None and self._refresh_token is not None

    @property
    def account_id(self) -> str | None:
        return self._account_id

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing if needed.

        Raises RuntimeError if not logged in or refresh fails.
        """
        if not self.is_logged_in:
            raise RuntimeError("Not logged in. Run: tina login openai")

        # Refresh if expiring within margin
        if time.time() >= self._expires_at - REFRESH_MARGIN_SECONDS:
            await self._refresh()

        return self._access_token

    async def _refresh(self):
        """Refresh the access token using the refresh token."""
        logger.info("Refreshing OpenAI OAuth token...")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": self._refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                error_detail = resp.text[:500]
                logger.error(f"Token refresh failed ({resp.status_code}): {error_detail}")
                raise RuntimeError(
                    f"Token refresh failed ({resp.status_code}). "
                    "Try logging in again: tina login openai"
                )
            tokens = resp.json()

        self._access_token = tokens["access_token"]
        if "refresh_token" in tokens:
            self._refresh_token = tokens["refresh_token"]
        self._expires_at = time.time() + tokens.get("expires_in", 3600)
        self._account_id = _extract_account_id(self._access_token) or self._account_id
        self._save()
        logger.info("Token refreshed successfully")

    def login(self) -> bool:
        """Run the interactive OAuth PKCE login flow.

        Opens browser, waits for callback, exchanges code for tokens.
        Returns True on success, False on failure.
        """
        verifier, challenge = _generate_pkce()
        state = secrets.token_urlsafe(16)

        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "audience": AUDIENCE,
        }
        auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

        # Callback handler
        received_code: list[str] = []
        received_state: list[str] = []
        callback_event = Event()

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                code = qs.get("code", [None])[0]
                cb_state = qs.get("state", [None])[0]

                if code:
                    received_code.append(code)
                    received_state.append(cb_state or "")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h2>Login successful!</h2>"
                        b"<p>You can close this tab and return to the terminal.</p>"
                        b"</body></html>"
                    )
                else:
                    error = qs.get("error", ["unknown"])[0]
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        f"<html><body><h2>Login failed: {error}</h2></body></html>".encode()
                    )
                callback_event.set()

            def log_message(self, format, *args):
                pass  # Suppress HTTP server logs

        # Start callback server
        try:
            server = HTTPServer(("127.0.0.1", CALLBACK_PORT), CallbackHandler)
        except OSError as e:
            logger.error(f"Cannot start callback server on port {CALLBACK_PORT}: {e}")
            print(f"Error: Cannot bind to port {CALLBACK_PORT}. Is another process using it?")
            return False

        server.timeout = 1  # 1-second poll timeout for handle_request

        # Open browser
        print(f"Opening browser for OpenAI login...")
        print(f"If the browser doesn't open, visit:\n{auth_url}\n")
        _open_browser(auth_url)

        # Wait for callback (max 120 seconds)
        print("Waiting for authorization...")
        deadline = time.time() + 120
        try:
            while not callback_event.is_set() and time.time() < deadline:
                server.handle_request()
        except KeyboardInterrupt:
            print("\nLogin cancelled.")
            server.server_close()
            return False
        finally:
            server.server_close()

        if not received_code:
            print("Login timed out or failed. No authorization code received.")
            return False

        # Verify state
        if received_state[0] != state:
            print("Login failed: state mismatch (possible CSRF attack).")
            return False

        # Exchange code for tokens
        print("Exchanging authorization code for tokens...")
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "client_id": CLIENT_ID,
                        "code": received_code[0],
                        "redirect_uri": REDIRECT_URI,
                        "code_verifier": verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code != 200:
                    print(f"Token exchange failed ({resp.status_code}): {resp.text[:200]}")
                    return False
                tokens = resp.json()
        except Exception as e:
            print(f"Token exchange error: {e}")
            return False

        self._access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token")
        self._expires_at = time.time() + tokens.get("expires_in", 3600)
        self._account_id = _extract_account_id(self._access_token)
        self._save()

        acct = self._account_id or "unknown"
        print(f"Login successful! Account: {acct}")
        return True

    def logout(self):
        """Clear stored tokens."""
        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0.0
        self._account_id = None
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
