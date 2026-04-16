"""Apple Music library: fetch user's library/playlists via Apple Music API."""

import re
import json
import logging
from datetime import datetime, timezone

import httpx

from ipod_sync.config import load_library_index, save_library_index

logger = logging.getLogger("ipod-sync")

AMP_API = "https://amp-api.music.apple.com"
HOMEPAGE = "https://music.apple.com"

HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US",
    "origin": HOMEPAGE,
    "referer": HOMEPAGE,
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    ),
}


class LibraryError(Exception):
    pass


class AppleMusicClient:
    """Authenticated Apple Music API client."""

    def __init__(self, cookies_path: str):
        self.media_user_token = self._read_token(cookies_path)
        self.dev_token = self._get_developer_token()
        self.client = httpx.Client(
            headers={**HEADERS, "authorization": f"Bearer {self.dev_token}"},
            cookies={"media-user-token": self.media_user_token},
            follow_redirects=True,
            timeout=30,
        )
        self.storefront = self._get_storefront()
        logger.info(f"Storefront: {self.storefront}")

    def close(self):
        self.client.close()

    def _read_token(self, cookies_path: str) -> str:
        with open(cookies_path) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                if len(parts) >= 7 and parts[5] == "media-user-token":
                    return parts[6]
        raise LibraryError("media-user-token not found in cookies.txt")

    def _get_developer_token(self) -> str:
        c = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)
        r = c.get(HOMEPAGE)
        match = re.search(r"/(assets/index-legacy[~-][^/\"]+\.js)", r.text)
        if not match:
            raise LibraryError("index.js not found in Apple Music web player")
        r = c.get(f"{HOMEPAGE}/{match.group(1)}")
        tok = re.search(r'(?=eyJh)(.*?)(?=")', r.text)
        if not tok:
            raise LibraryError("Developer token not found")
        c.close()
        return tok.group(1)

    def _get_storefront(self) -> str:
        r = self.client.get(f"{AMP_API}/v1/me/account", params={"meta": "subscription"})
        if r.status_code != 200:
            raise LibraryError(f"Account error: {r.status_code}")
        return r.json().get("meta", {}).get("subscription", {}).get("storefront", "us")

    def _api(self, endpoint: str, params: dict | None = None) -> dict:
        r = self.client.get(f"{AMP_API}{endpoint}", params=params or {})
        if r.status_code in (401, 403):
            raise LibraryError("Session expired. Re-export your cookies from music.apple.com.")
        if r.status_code != 200:
            raise LibraryError(f"API error {r.status_code}: {r.text[:200]}")
        return r.json()

    # --- Playlists ---

    def list_playlists(self) -> list[dict]:
        """List all user library playlists."""
        playlists = []
        offset = 0
        while True:
            data = self._api("/v1/me/library/playlists", {"limit": 25, "offset": offset})
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                attrs = item.get("attributes", {})
                playlists.append({
                    "id": item["id"],
                    "name": attrs.get("name", "Sin nombre"),
                    "track_count": attrs.get("trackCount", 0) if "trackCount" in attrs else None,
                })
            if not data.get("next"):
                break
            offset += len(items)
        return playlists

    def get_playlist_tracks(self, playlist_id: str, limit: int = 50) -> list[dict]:
        """Fetch tracks from a library playlist."""
        tracks = []
        offset = 0
        while len(tracks) < limit:
            fetch = min(25, limit - len(tracks))
            data = self._api(
                f"/v1/me/library/playlists/{playlist_id}/tracks",
                {"limit": fetch, "offset": offset, "include": "catalog"},
            )
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                if len(tracks) >= limit:
                    break
                tracks.append(self._parse_track(item))
            if not data.get("next"):
                break
            offset += len(items)
        return tracks

    # --- Library songs ---

    def get_library_songs(self, limit: int = 50) -> list[dict]:
        """Fetch songs from user's library (recently added first)."""
        tracks = []
        offset = 0
        while len(tracks) < limit:
            fetch = min(25, limit - len(tracks))
            data = self._api(
                "/v1/me/library/songs",
                {"limit": fetch, "offset": offset, "include": "catalog"},
            )
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                if len(tracks) >= limit:
                    break
                tracks.append(self._parse_track(item))
            if not data.get("next"):
                break
            offset += len(items)
        return tracks

    def find_playlist(self, name: str) -> dict | None:
        """Find a playlist by name (case-insensitive partial match)."""
        playlists = self.list_playlists()
        name_lower = name.lower()
        # Exact match first
        for p in playlists:
            if p["name"].lower() == name_lower:
                return p
        # Partial match
        for p in playlists:
            if name_lower in p["name"].lower():
                return p
        return None

    def _parse_track(self, item: dict) -> dict:
        attrs = item.get("attributes", {})
        play_params = attrs.get("playParams", {})
        catalog_id = play_params.get("catalogId") or play_params.get("id", "")

        catalog_url = ""
        catalog_data = (
            item.get("relationships", {}).get("catalog", {}).get("data", [])
        )
        if catalog_data:
            cat_attrs = catalog_data[0].get("attributes", {})
            catalog_url = cat_attrs.get("url", "")
            catalog_id = catalog_id or catalog_data[0].get("id", "")

        if not catalog_url and catalog_id:
            catalog_url = f"{HOMEPAGE}/{self.storefront}/song/{catalog_id}"

        return {
            "id": catalog_id,
            "library_id": item.get("id", ""),
            "artist": attrs.get("artistName", "Unknown Artist"),
            "album": attrs.get("albumName", "Unknown Album"),
            "title": attrs.get("name", "Unknown"),
            "duration_ms": attrs.get("durationInMillis", 0),
            "genre": (attrs.get("genreNames") or [""])[0],
            "track_number": attrs.get("trackNumber", 0),
            "url": catalog_url,
            "storefront": self.storefront,
        }


# --- Index management ---

def _track_key(artist: str, album: str, title: str) -> str:
    return f"{artist}|{album}|{title}".lower().strip()


def compute_diff(remote_tracks: list[dict]) -> tuple[list[dict], list[str]]:
    index = load_library_index()
    local_keys = set(index["tracks"].keys())
    remote_by_key = {}
    for t in remote_tracks:
        key = _track_key(t["artist"], t["album"], t["title"])
        remote_by_key[key] = t
    to_download = [t for key, t in remote_by_key.items() if key not in local_keys]
    return to_download, []


def mark_downloaded(track: dict, file_path: str) -> None:
    index = load_library_index()
    key = _track_key(track["artist"], track["album"], track["title"])
    index["tracks"][key] = {
        "id": track["id"],
        "artist": track["artist"],
        "album": track["album"],
        "title": track["title"],
        "file": file_path,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    index["last_sync"] = datetime.now(timezone.utc).isoformat()
    save_library_index(index)


def save_playlist(name: str, track_keys: list[str]) -> None:
    """Persist a named playlist (list of track keys) in library.json."""
    index = load_library_index()
    if "playlists" not in index:
        index["playlists"] = {}
    index["playlists"][name] = track_keys
    save_library_index(index)
