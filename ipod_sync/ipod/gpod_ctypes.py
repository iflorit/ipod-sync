"""libgpod wrapper via ctypes — correct iTunesDB read/write for iPod Classic."""

import ctypes
import ctypes.util
import os
from pathlib import Path


class GpodError(Exception):
    pass


def _find_libgpod() -> str:
    """Find the libgpod shared library."""
    candidates = [
        "/tmp/libgpod-install/usr/local/lib/libgpod.4.dylib",  # macOS build
        "/usr/local/lib/libgpod.4.dylib",
        "/usr/lib/libgpod.so.4",
        "/usr/lib/aarch64-linux-gnu/libgpod.so.4",
        "/usr/lib/arm-linux-gnueabihf/libgpod.so.4",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    found = ctypes.util.find_library("gpod")
    if found:
        return found
    raise GpodError(
        "libgpod not found. Install with:\n"
        "  Linux: apt install libgpod-dev\n"
        "  macOS: build from source (see scripts/install-mac.sh)"
    )


# Load library
_lib_path = _find_libgpod()
_lib = ctypes.CDLL(_lib_path)

# Also need glib for g_strdup / g_error_free
_glib_path = ctypes.util.find_library("glib-2.0")
if _glib_path:
    _glib = ctypes.CDLL(_glib_path)
else:
    _glib = ctypes.CDLL("/opt/homebrew/lib/libglib-2.0.dylib")


# --- Type definitions ---
class GError(ctypes.Structure):
    _fields_ = [
        ("domain", ctypes.c_uint32),
        ("code", ctypes.c_int),
        ("message", ctypes.c_char_p),
    ]


GErrorPtr = ctypes.POINTER(GError)

Itdb_iTunesDB_p = ctypes.c_void_p
Itdb_Track_p = ctypes.c_void_p
Itdb_Playlist_p = ctypes.c_void_p


# --- Function signatures ---

_lib.itdb_parse.restype = Itdb_iTunesDB_p
_lib.itdb_parse.argtypes = [ctypes.c_char_p, ctypes.POINTER(GErrorPtr)]

_lib.itdb_new.restype = Itdb_iTunesDB_p

_lib.itdb_set_mountpoint.argtypes = [Itdb_iTunesDB_p, ctypes.c_char_p]

_lib.itdb_write.restype = ctypes.c_int
_lib.itdb_write.argtypes = [Itdb_iTunesDB_p, ctypes.POINTER(GErrorPtr)]

_lib.itdb_track_new.restype = Itdb_Track_p

_lib.itdb_track_add.argtypes = [Itdb_iTunesDB_p, Itdb_Track_p, ctypes.c_int32]

_lib.itdb_cp_track_to_ipod.restype = ctypes.c_int
_lib.itdb_cp_track_to_ipod.argtypes = [Itdb_Track_p, ctypes.c_char_p, ctypes.POINTER(GErrorPtr)]

_lib.itdb_playlist_mpl.restype = Itdb_Playlist_p
_lib.itdb_playlist_mpl.argtypes = [Itdb_iTunesDB_p]

_lib.itdb_playlist_add_track.argtypes = [Itdb_Playlist_p, Itdb_Track_p, ctypes.c_int32]

_lib.itdb_playlist_new.restype = Itdb_Playlist_p
_lib.itdb_playlist_new.argtypes = [ctypes.c_char_p, ctypes.c_int]

_lib.itdb_playlist_add.argtypes = [Itdb_iTunesDB_p, Itdb_Playlist_p, ctypes.c_int32]

_lib.itdb_playlist_set_mpl.argtypes = [Itdb_Playlist_p]

_lib.itdb_free.argtypes = [Itdb_iTunesDB_p]

# g_strdup: allocates a glib-managed copy of a C string
_glib.g_strdup.restype = ctypes.c_void_p   # returns address (not c_char_p, to get int)
_glib.g_strdup.argtypes = [ctypes.c_char_p]

_glib.g_error_free.argtypes = [GErrorPtr]

# itdb_track_set_thumbnails_from_data — requires gdk-pixbuf; no-op if not built with it
_lib.itdb_track_set_thumbnails_from_data.restype = ctypes.c_int
_lib.itdb_track_set_thumbnails_from_data.argtypes = [
    Itdb_Track_p, ctypes.c_char_p, ctypes.c_size_t
]


# --- Itdb_Track field offsets (verified via ctypes.Structure alignment on 64-bit) ---
# All pointer fields are 8 bytes. Alignment as per System V AMD64 ABI.
_TRACK_OFF = {
    "title":       8,
    "ipod_path":   16,
    "album":       24,
    "artist":      32,
    "genre":       40,
    "tracklen":    224,   # gint32 — duration in ms
    "track_nr":    236,   # gint32
    "bitrate":     244,   # gint32 — kbps
    "samplerate":  248,   # guint16
    "visible":     348,   # guint32 — 1 = visible
    "mediatype":   460,   # guint32 — 0x01 = audio
}


def _set_str(track_ptr: int, offset: int, value: str) -> None:
    """Write a glib-managed gchar* string to an Itdb_Track field."""
    if not value:
        return
    addr = _glib.g_strdup(value.encode("utf-8"))
    if not addr:
        return
    ctypes.memmove(
        track_ptr + offset,
        ctypes.byref(ctypes.c_void_p(addr)),
        ctypes.sizeof(ctypes.c_void_p),
    )


def _set_i32(track_ptr: int, offset: int, value: int) -> None:
    ctypes.memmove(track_ptr + offset, ctypes.byref(ctypes.c_int32(value)), 4)


def _set_u32(track_ptr: int, offset: int, value: int) -> None:
    ctypes.memmove(track_ptr + offset, ctypes.byref(ctypes.c_uint32(value)), 4)


def _set_u16(track_ptr: int, offset: int, value: int) -> None:
    ctypes.memmove(track_ptr + offset, ctypes.byref(ctypes.c_uint16(value)), 2)


def _read_m4a_tags(file_path: str) -> dict:
    """Read tags and audio info from a .m4a file using mutagen."""
    info = {
        "title": "",
        "artist": "",
        "album": "",
        "genre": "",
        "track_nr": 0,
        "tracklen": 0,
        "bitrate": 0,
        "samplerate": 44100,
        "artwork_bytes": None,
    }
    if not file_path or not Path(file_path).exists():
        return info
    try:
        from mutagen.mp4 import MP4
        audio = MP4(file_path)
        tags = audio.tags or {}

        def _tag(key):
            v = tags.get(key)
            return str(v[0]) if v else ""

        info["title"] = _tag("\xa9nam")
        info["artist"] = _tag("\xa9ART")
        info["album"] = _tag("\xa9alb")
        info["genre"] = _tag("\xa9gen")

        trkn = tags.get("trkn")
        if trkn and isinstance(trkn[0], (tuple, list)):
            info["track_nr"] = int(trkn[0][0]) if trkn[0][0] else 0

        info["tracklen"] = int(audio.info.length * 1000)
        info["bitrate"] = (audio.info.bitrate or 0) // 1000
        info["samplerate"] = audio.info.sample_rate or 44100

        # Embedded cover art (JPEG or PNG bytes)
        covr = tags.get("covr")
        if covr:
            info["artwork_bytes"] = bytes(covr[0])
    except Exception:
        pass
    return info


def _apply_track_metadata(track_ptr: int, meta: dict) -> None:
    """Write metadata dict to Itdb_Track struct fields."""
    _set_str(track_ptr, _TRACK_OFF["title"], meta.get("title", ""))
    _set_str(track_ptr, _TRACK_OFF["artist"], meta.get("artist", ""))
    _set_str(track_ptr, _TRACK_OFF["album"], meta.get("album", ""))
    _set_str(track_ptr, _TRACK_OFF["genre"], meta.get("genre", ""))

    tracklen = meta.get("tracklen", 0)
    if tracklen:
        _set_i32(track_ptr, _TRACK_OFF["tracklen"], tracklen)

    track_nr = meta.get("track_nr", 0)
    if track_nr:
        _set_i32(track_ptr, _TRACK_OFF["track_nr"], track_nr)

    bitrate = meta.get("bitrate", 0)
    if bitrate:
        _set_i32(track_ptr, _TRACK_OFF["bitrate"], bitrate)

    samplerate = meta.get("samplerate", 44100)
    _set_u16(track_ptr, _TRACK_OFF["samplerate"], samplerate)

    _set_u32(track_ptr, _TRACK_OFF["visible"], 1)
    _set_u32(track_ptr, _TRACK_OFF["mediatype"], 0x00000001)  # audio


def _try_set_artwork(track_ptr: int, artwork_bytes: bytes | None) -> None:
    """Attempt to set cover art on track (requires gdk-pixbuf in libgpod build)."""
    if not artwork_bytes:
        return
    try:
        data_buf = ctypes.create_string_buffer(artwork_bytes, len(artwork_bytes))
        result = _lib.itdb_track_set_thumbnails_from_data(
            track_ptr,
            data_buf,
            ctypes.c_size_t(len(artwork_bytes)),
        )
        # result == 0 means failure (gdk-pixbuf not available) — silently ignore
    except Exception:
        pass


def _get_gerror_message(err_ptr: GErrorPtr) -> str:
    msg = ""
    if err_ptr:
        try:
            if err_ptr.contents.message:
                msg = err_ptr.contents.message.decode("utf-8", errors="replace")
        except Exception:
            pass
        _glib.g_error_free(err_ptr)
    return msg


def _create_fresh_db(mount_bytes: bytes) -> int:
    """Create a new empty iTunesDB with a valid master playlist."""
    db = _lib.itdb_new()
    if not db:
        raise GpodError("itdb_new() returned NULL")

    _lib.itdb_set_mountpoint(db, mount_bytes)

    mpl = _lib.itdb_playlist_new(b"iPod", 0)
    if not mpl:
        _lib.itdb_free(db)
        raise GpodError("itdb_playlist_new() returned NULL")

    _lib.itdb_playlist_set_mpl(mpl)
    _lib.itdb_playlist_add(db, mpl, -1)
    return db


def sync_tracks_to_ipod(
    ipod_mount: str,
    tracks: list[dict],
    playlists: dict[str, list[int]] | None = None,
) -> int:
    """Sync tracks to iPod using libgpod.

    Creates a fresh iTunesDB each time (clean slate).
    Reads metadata from source files via mutagen and writes to track structs.

    Returns: number of tracks successfully copied.
    """
    mount_bytes = ipod_mount.encode("utf-8")

    # Delete existing iTunesDB to force a clean rebuild
    db_path = Path(ipod_mount) / "iPod_Control" / "iTunes" / "iTunesDB"
    if db_path.exists():
        db_path.unlink()

    # Always start from a fresh DB
    db = _create_fresh_db(mount_bytes)

    mpl = _lib.itdb_playlist_mpl(db)
    if not mpl:
        _lib.itdb_free(db)
        raise GpodError("Master playlist not found after initialization")

    # Build named playlists if requested
    playlist_ptrs: dict[str, int] = {}
    if playlists:
        for pl_name in playlists:
            pl = _lib.itdb_playlist_new(pl_name.encode("utf-8"), 0)
            if pl:
                _lib.itdb_playlist_add(db, pl, -1)
                playlist_ptrs[pl_name] = pl

    copied = 0
    track_ptrs: list[int | None] = []

    for idx, track_info in enumerate(tracks):
        src_path = track_info.get("file", "")
        if not src_path or not Path(src_path).exists():
            track_ptrs.append(None)
            continue

        # Read metadata from the source file
        meta = _read_m4a_tags(src_path)

        # Fall back to dict values if mutagen found nothing
        if not meta["title"]:
            meta["title"] = track_info.get("title", "")
        if not meta["artist"]:
            meta["artist"] = track_info.get("artist", "")
        if not meta["album"]:
            meta["album"] = track_info.get("album", "")
        if not meta["genre"]:
            meta["genre"] = track_info.get("genre", "")
        if not meta["track_nr"] and track_info.get("track_number"):
            meta["track_nr"] = track_info["track_number"]

        # Create track object, set metadata, register in DB
        track = _lib.itdb_track_new()
        if not track:
            track_ptrs.append(None)
            continue

        _apply_track_metadata(track, meta)
        _try_set_artwork(track, meta.get("artwork_bytes"))

        _lib.itdb_track_add(db, track, -1)
        _lib.itdb_playlist_add_track(mpl, track, -1)

        # Copy file to iPod (sets ipod_path on the track struct)
        cp_error = GErrorPtr()
        result = _lib.itdb_cp_track_to_ipod(track, src_path.encode("utf-8"), ctypes.byref(cp_error))

        if result:
            copied += 1
            track_ptrs.append(track)
        else:
            err_msg = _get_gerror_message(cp_error)
            print(f"  Error copiando {Path(src_path).name}: {err_msg}")
            track_ptrs.append(None)

    # Add tracks to named playlists
    if playlists and playlist_ptrs:
        for pl_name, indices in playlists.items():
            pl = playlist_ptrs.get(pl_name)
            if not pl:
                continue
            for i in indices:
                if i < len(track_ptrs) and track_ptrs[i] is not None:
                    _lib.itdb_playlist_add_track(pl, track_ptrs[i], -1)

    # Write iTunesDB
    write_error = GErrorPtr()
    success = _lib.itdb_write(db, ctypes.byref(write_error))

    if not success:
        err_msg = _get_gerror_message(write_error)
        _lib.itdb_free(db)
        raise GpodError(f"Error escribiendo iTunesDB: {err_msg}")

    _lib.itdb_free(db)
    return copied
