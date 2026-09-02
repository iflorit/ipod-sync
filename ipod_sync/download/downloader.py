"""Download tracks using gamdl as backend."""

import subprocess
import json
from pathlib import Path

from ipod_sync.config import Config, CONFIG_DIR


class DownloadError(Exception):
    pass


def download_track(config: Config, track: dict, cookies_path: str) -> str:
    """Download a single track via gamdl CLI.

    Returns path to the downloaded .m4a file.
    """
    url = track.get("url", "")
    if not url:
        raise DownloadError(f"No URL for '{track['title']}'")

    output_dir = config.music_dir

    result = subprocess.run(
        [
            "gamdl",
            "--no-config-file",  # deterministic: ignore ~/.gamdl/config.ini leftovers
            "--cookies-path", cookies_path,
            "--output-path", output_dir,
            "--log-level", "WARNING",
            "--no-exceptions",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "already exists" in stderr.lower():
            pass  # File exists, find it
        else:
            raise DownloadError(f"gamdl error: {stderr[:200]}")

    # Find the downloaded file
    found = _find_track_file(output_dir, track)
    if found:
        return str(found)

    raise DownloadError(f"File not found after download: {track['title']}")


def download_tracks_batch(config: Config, tracks: list[dict], cookies_path: str) -> list[str]:
    """Download multiple tracks by collecting unique album URLs and batch-downloading.

    gamdl is more efficient downloading full albums than individual songs.
    Returns list of downloaded file paths.
    """
    # Group tracks by album URL (download albums at once for efficiency)
    album_urls = set()
    for t in tracks:
        url = t.get("url", "")
        if not url:
            continue
        # Convert song URL to album URL if possible
        # Song URLs look like: .../album/name/123?i=456
        # Album URLs look like: .../album/name/123
        if "?i=" in url:
            album_url = url.split("?i=")[0]
        else:
            album_url = url
        album_urls.add(album_url)

    if not album_urls:
        return []

    # Download all albums at once via gamdl
    urls_file = CONFIG_DIR / "download_urls.txt"
    urls_file.write_text("\n".join(album_urls))

    result = subprocess.run(
        [
            "gamdl",
            "--no-config-file",  # deterministic: ignore ~/.gamdl/config.ini leftovers
            "--cookies-path", cookies_path,
            "--output-path", config.music_dir,
            "--log-level", "INFO",
            "--no-exceptions",
            "--read-urls-as-txt",
            str(urls_file),
        ],
        capture_output=True,
        text=True,
        timeout=3600,  # 1 hour for large batches
    )

    urls_file.unlink(missing_ok=True)

    # Find all downloaded files
    paths = []
    for t in tracks:
        found = _find_track_file(config.music_dir, t)
        if found:
            paths.append(str(found))

    return paths


def _fold(s: str) -> str:
    """Lowercase and drop everything but letters/digits.

    gamdl sanitizes filenames (drops ? : / " etc.), so comparing raw titles
    against filenames fails for e.g. "Re: Stacks" or "What If I Told You?".
    """
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _find_track_file(base_dir: str, track: dict) -> Path | None:
    """Find a downloaded track file by searching the output directory."""
    base = Path(base_dir)
    title = _fold(track.get("title", ""))
    artist_words = track.get("artist", "").lower().split()
    artist = _fold(artist_words[0]) if artist_words else ""
    if not title:
        return None

    # gamdl uses template: {album_artist}/{album}/{track:02d} {title}.m4a
    candidates = [m4a for m4a in base.rglob("*.m4a") if title in _fold(m4a.stem)]

    # Prefer a match whose path also contains the artist
    for m4a in candidates:
        if artist and artist in _fold(str(m4a)):
            return m4a

    return candidates[0] if candidates else None


def verify_track(file_path: str) -> bool:
    """Verify a downloaded track is valid."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", file_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False
        probe = json.loads(result.stdout)
        has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))
        duration = float(probe.get("format", {}).get("duration", 0))
        return has_audio and duration > 0
    except Exception:
        return False
