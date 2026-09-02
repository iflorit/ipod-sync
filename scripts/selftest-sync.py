#!/usr/bin/env python3
"""End-to-end libgpod self-test without a physical iPod.

Builds a mock iPod directory tree, generates a tiny tagged .m4a with ffmpeg,
syncs it through gpod_ctypes.sync_tracks_to_ipod(), then parses the written
iTunesDB back with libgpod and checks the track count.

Usage: python3 scripts/selftest-sync.py [--keep]
"""

import ctypes
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ipod_sync.ipod import gpod_ctypes
from ipod_sync.ipod.gpod_ctypes import GpodError, sync_tracks_to_ipod


def make_mock_ipod(root: Path) -> None:
    (root / "iPod_Control" / "iTunes").mkdir(parents=True)
    (root / "iPod_Control" / "Device").mkdir(parents=True)
    (root / "iPod_Control" / "Device" / "SysInfo").write_text(
        "ModelNumStr: xA002\nBoardHwName: iPod Video\n"
    )
    for i in range(50):
        (root / "iPod_Control" / "Music" / f"F{i:02d}").mkdir(parents=True)


def make_track(path: Path, title: str, artist: str, album: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1",
            "-c:a", "aac", "-b:a", "64k",
            "-metadata", f"title={title}", "-metadata", f"artist={artist}",
            "-metadata", f"album={album}",
            str(path),
        ],
        check=True,
    )


def count_tracks(mount: Path) -> int:
    lib = gpod_ctypes._lib
    err = gpod_ctypes.GErrorPtr()
    db = lib.itdb_parse(str(mount).encode(), ctypes.byref(err))
    if not db:
        raise GpodError("itdb_parse failed: " + gpod_ctypes._get_gerror_message(err))
    lib.itdb_tracks_number.restype = ctypes.c_uint32
    lib.itdb_tracks_number.argtypes = [ctypes.c_void_p]
    n = lib.itdb_tracks_number(db)
    lib.itdb_playlists_number.restype = ctypes.c_uint32
    lib.itdb_playlists_number.argtypes = [ctypes.c_void_p]
    p = lib.itdb_playlists_number(db)
    print(f"itdb_parse: {n} track(s), {p} playlist(s) (incl. master)")
    return n


def main() -> int:
    keep = "--keep" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="ipod-selftest-"))
    mount = work / "ipod"
    try:
        print(f"libgpod: {gpod_ctypes._lib_path}")
        make_mock_ipod(mount)
        tracks = []
        for i in range(3):
            f = work / f"track{i}.m4a"
            make_track(f, f"Selftest {i}", "ipod-sync", "Selftest Album")
            tracks.append({
                "title": f"Selftest {i}", "artist": "ipod-sync",
                "album": "Selftest Album", "genre": "Test",
                "track_number": i + 1, "duration_ms": 1000, "file": str(f),
            })
        added = sync_tracks_to_ipod(str(mount), tracks, playlists={"Selftest": [0, 2]})
        print(f"sync_tracks_to_ipod: {added} added")
        copied = list((mount / "iPod_Control" / "Music").rglob("*.m4a"))
        print(f"files on mock iPod: {len(copied)}")
        n = count_tracks(mount)
        ok = added == len(tracks) == len(copied) == n
        print("SELFTEST", "OK" if ok else "FAILED")
        return 0 if ok else 1
    finally:
        if keep:
            print(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
