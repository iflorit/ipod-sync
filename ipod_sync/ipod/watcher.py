"""Non-blocking iPod connection monitoring via polling."""

import time

from ipod_sync.ipod.detect import detect_ipod

POLL_INTERVAL = 5  # seconds


def find_ipod_mount() -> str | None:
    """Return iPod mount point if connected and mounted, else None.

    If the iPod is connected but not mounted (NOT_MOUNTED), attempts to
    mount it automatically so the daemon works regardless of udev state.
    """
    result = detect_ipod()
    if result is None:
        return None
    if result == "NOT_MOUNTED":
        try:
            from ipod_sync.ipod.mount import mount_ipod
            return mount_ipod()
        except Exception:
            return None
    return result


def wait_for_ipod(stop_event=None, poll_interval: int = POLL_INTERVAL) -> str | None:
    """Block until iPod is mounted and accessible.

    Returns the mount point, or None if stop_event is set before detection.
    """
    while True:
        if stop_event and stop_event.is_set():
            return None
        mount = find_ipod_mount()
        if mount:
            return mount
        time.sleep(poll_interval)


def wait_for_disconnect(mount_point: str, stop_event=None, poll_interval: int = POLL_INTERVAL) -> None:
    """Block until the iPod at mount_point is no longer detected."""
    while True:
        if stop_event and stop_event.is_set():
            return
        current = find_ipod_mount()
        if current != mount_point:
            return
        time.sleep(poll_interval)
