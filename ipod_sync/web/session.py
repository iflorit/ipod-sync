"""Apple Music session (media-user-token) lifecycle.

Apple does not offer a refresh mechanism: the token is minted by a logged-in
browser and expires after ~6 months. This module receives a token (sent by the
bookmarklet from the iPhone), validates it against the AMP API, persists it as
the Netscape cookies file that both our client and gamdl read, and tracks when
it was issued so the daemon can warn before expiry.
"""

import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ipod_sync.config import CONFIG_DIR, COOKIES_FILE

logger = logging.getLogger("session")

SESSION_FILE = CONFIG_DIR / "session.json"
TOKEN_LIFETIME = timedelta(days=180)  # documented by Apple: 6 months, not configurable
WARN_BEFORE = timedelta(days=30)


class SessionError(Exception):
    pass


def _cookies_text(token: str) -> str:
    expiry = int((datetime.now(timezone.utc) + TOKEN_LIFETIME).timestamp())
    return (
        "# Netscape HTTP Cookie File\n"
        f".music.apple.com\tTRUE\t/\tTRUE\t{expiry}\tmedia-user-token\t{token}\n"
    )


def validate_token(token: str) -> dict:
    """Return {"storefront", "playlists"} if the token authenticates, else raise."""
    from ipod_sync.download.library import AppleMusicClient, LibraryError

    token = token.strip()
    if not token or any(c.isspace() for c in token):
        raise SessionError("Token vacío o con espacios")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(_cookies_text(token))
        tmp = f.name
    try:
        am = AppleMusicClient(tmp)
        try:
            playlists = am.list_playlists()
            return {"storefront": am.storefront, "playlists": len(playlists)}
        finally:
            am.close()
    except LibraryError as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "expired" in msg.lower():
            msg = "Apple rechaza el token (401/403): inicia sesión en music.apple.com y vuelve a tocar el marcador"
        raise SessionError(msg) from e
    finally:
        Path(tmp).unlink(missing_ok=True)


def save_token(token: str, info: dict) -> dict:
    """Persist a validated token: cookies.txt (+ .bak of previous) and session.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if COOKIES_FILE.exists():
        COOKIES_FILE.replace(COOKIES_FILE.with_suffix(".txt.bak"))
    COOKIES_FILE.write_text(_cookies_text(token.strip()))
    now = datetime.now(timezone.utc)
    state = {
        "validated_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + TOKEN_LIFETIME).isoformat(timespec="seconds"),
        "storefront": info.get("storefront"),
        "playlists": info.get("playlists"),
        "last_error": None,
    }
    SESSION_FILE.write_text(json.dumps(state, indent=2))
    logger.info(f"Apple Music session renewed (storefront {state['storefront']}, "
                f"{state['playlists']} playlists) — expires {state['expires_at'][:10]}")
    return state


def renew(token: str) -> dict:
    return save_token(token, validate_token(token))


def record_error(message: str) -> None:
    state = load_state()
    state["last_error"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "message": message}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(state, indent=2))


def load_state() -> dict:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def status() -> dict:
    """Summary for the portal / CLI: has_cookies, expires_at, days_left, level."""
    state = load_state()
    has_cookies = COOKIES_FILE.exists()
    expires = state.get("expires_at")
    days_left = None
    if expires:
        days_left = (datetime.fromisoformat(expires) - datetime.now(timezone.utc)).days
    elif has_cookies:
        # Cookies imported before session tracking existed: estimate from file mtime
        mtime = datetime.fromtimestamp(COOKIES_FILE.stat().st_mtime, timezone.utc)
        days_left = (mtime + TOKEN_LIFETIME - datetime.now(timezone.utc)).days
        expires = (mtime + TOKEN_LIFETIME).isoformat(timespec="seconds")

    if not has_cookies:
        level = "missing"
    elif state.get("last_error"):
        level = "expired"
    elif days_left is not None and days_left <= 0:
        level = "expired"
    elif days_left is not None and days_left <= WARN_BEFORE.days:
        level = "expiring"
    else:
        level = "ok"
    return {
        "has_cookies": has_cookies,
        "validated_at": state.get("validated_at"),
        "expires_at": expires,
        "days_left": days_left,
        "storefront": state.get("storefront"),
        "playlists": state.get("playlists"),
        "last_error": state.get("last_error"),
        "level": level,
    }
