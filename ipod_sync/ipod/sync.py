"""Sync local music library to iPod Classic."""

from pathlib import Path

from ipod_sync.config import Config, load_library_index
from ipod_sync.ipod.gpod_ctypes import sync_tracks_to_ipod


class SyncError(Exception):
    pass


def _clean_ipod_music(ipod_mount: str) -> int:
    """Remove all audio files from iPod Music directories. Returns count removed."""
    music_dir = Path(ipod_mount) / "iPod_Control" / "Music"
    removed = 0
    if not music_dir.exists():
        return 0
    for fdir in music_dir.iterdir():
        if not fdir.is_dir() or not fdir.name.startswith("F"):
            continue
        for f in fdir.iterdir():
            if f.suffix.lower() in (".m4a", ".mp3", ".aac", ".mp4"):
                f.unlink()
                removed += 1
    return removed


def sync_to_ipod(ipod_mount: str, config: Config, on_progress=None) -> tuple[int, int]:
    """Sync local library to iPod.

    Cleans old files, copies audio files via libgpod, writes iTunesDB.
    Returns (added_count, removed_count).
    """
    ipod_control = Path(ipod_mount) / "iPod_Control"
    if not ipod_control.exists():
        raise SyncError(f"iPod_Control not found at {ipod_mount}")

    index = load_library_index()
    local_tracks = index.get("tracks", {})
    if not local_tracks:
        raise SyncError("Local library is empty. Run 'ipod-sync download' first.")

    # Build ordered track list and a key→index map for playlist resolution
    tracks_to_sync = []
    key_to_index: dict[str, int] = {}
    for key, info in local_tracks.items():
        file_path = info.get("file", "")
        if not file_path or not Path(file_path).exists():
            if on_progress:
                on_progress("skip", info.get("title", key), 0, 0)
            continue
        key_to_index[key] = len(tracks_to_sync)
        tracks_to_sync.append({
            "title": info.get("title", "Unknown"),
            "artist": info.get("artist", "Unknown"),
            "album": info.get("album", "Unknown"),
            "genre": info.get("genre", ""),
            "track_number": info.get("track_number", 0),
            "duration_ms": info.get("duration_ms", 0),
            "file": file_path,
        })

    if not tracks_to_sync:
        raise SyncError("No valid audio files to sync.")

    # Resolve named playlists: keys → indices in tracks_to_sync
    playlists: dict[str, list[int]] | None = None
    raw_playlists = index.get("playlists", {})
    if raw_playlists:
        playlists = {}
        for pl_name, pl_keys in raw_playlists.items():
            indices = [key_to_index[k] for k in pl_keys if k in key_to_index]
            if indices:
                playlists[pl_name] = indices

    # Clean old audio files from iPod (gpod_ctypes will also delete the old DB)
    if on_progress:
        on_progress("info", "Cleaning iPod...", 0, 0)
    removed = _clean_ipod_music(ipod_mount)

    # Sync via libgpod — copies files and writes iTunesDB
    if on_progress:
        on_progress("info", f"Syncing {len(tracks_to_sync)} tracks via libgpod...", 0, 0)

    for i, t in enumerate(tracks_to_sync, 1):
        if on_progress:
            on_progress("add", t["title"], i, len(tracks_to_sync))

    added = sync_tracks_to_ipod(ipod_mount, tracks_to_sync, playlists=playlists)

    return added, removed
