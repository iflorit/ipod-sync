"""iPod Classic detection on USB."""

import platform
import subprocess
from pathlib import Path


APPLE_VENDOR_ID = "05ac"

# Known iPod Classic product IDs
IPOD_PRODUCT_IDS = {
    "1209",  # iPod Classic / iPod 5th gen
    "1261",  # iPod Classic 80GB/160GB
    "1262",  # iPod Classic 120GB
    "1263",  # iPod Classic 160GB (late 2009)
}


def detect_ipod() -> str | None:
    """Detect a connected iPod and return its mount point, or None.

    Works on both macOS and Linux.
    """
    system = platform.system()
    if system == "Darwin":
        return _detect_macos()
    elif system == "Linux":
        return _detect_linux()
    return None


def _detect_macos() -> str | None:
    """Detect iPod on macOS (auto-mounted in /Volumes/)."""
    # iPod typically mounts as /Volumes/IPOD or /Volumes/<iPod Name>
    volumes = Path("/Volumes")
    if not volumes.exists():
        return None

    for vol in volumes.iterdir():
        if not vol.is_dir():
            continue
        # Check for iPod_Control directory (signature of iPod filesystem)
        if (vol / "iPod_Control").exists():
            return str(vol)

    # Fallback: check system_profiler for Apple USB devices
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-detailLevel", "mini"],
            capture_output=True, text=True, timeout=10,
        )
        if "iPod" in result.stdout:
            # Re-scan volumes — it might be mounting
            for vol in volumes.iterdir():
                if vol.is_dir() and (vol / "iPod_Control").exists():
                    return str(vol)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def _detect_linux() -> str | None:
    """Detect iPod on Linux."""
    # Check common mount points
    for mount_point in ["/media/ipod", "/media/pi/ipod", "/mnt/ipod"]:
        mp = Path(mount_point)
        if mp.exists() and mp.is_mount() and (mp / "iPod_Control").exists():
            return str(mp)

    # Check /media/ for any volume with iPod_Control
    media = Path("/media")
    if media.exists():
        for user_dir in media.iterdir():
            if not user_dir.is_dir():
                continue
            for vol in user_dir.iterdir():
                if vol.is_dir() and (vol / "iPod_Control").exists():
                    return str(vol)

    # Check if iPod is connected but not mounted (via lsusb)
    try:
        result = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if APPLE_VENDOR_ID in line.lower():
                for pid in IPOD_PRODUCT_IDS:
                    if pid in line.lower():
                        # iPod connected but not mounted
                        return "NOT_MOUNTED"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


def is_ipod_connected() -> bool:
    """Quick check if any iPod is connected (mounted or not)."""
    return detect_ipod() is not None
