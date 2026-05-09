"""iPod mount/unmount operations."""

import platform
import subprocess
from pathlib import Path


class MountError(Exception):
    pass


def mount_ipod(device: str = "") -> str:
    """Mount iPod and return mount point.

    On macOS: iPod auto-mounts, just return the path.
    On Linux: use pmount or udisksctl.
    """
    system = platform.system()
    if system == "Darwin":
        return _mount_macos()
    elif system == "Linux":
        return _mount_linux(device)
    raise MountError(f"Unsupported platform: {system}")


def unmount_ipod(mount_point: str) -> None:
    """Safely unmount iPod."""
    system = platform.system()

    # Sync filesystem first
    subprocess.run(["sync"], timeout=30)

    if system == "Darwin":
        result = subprocess.run(
            ["diskutil", "eject", mount_point],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise MountError(f"Error ejecting iPod: {result.stderr}")
    elif system == "Linux":
        # HFS+ mounts require sudo umount; try that first, then pumount/udisksctl
        for cmd in [
            ["sudo", "umount", mount_point],
            ["pumount", mount_point],
            ["udisksctl", "unmount", "-b", mount_point],
        ]:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return
        raise MountError(f"Error unmounting iPod: {result.stderr}")


def _mount_macos() -> str:
    """On macOS, iPod auto-mounts. Find and return the path."""
    volumes = Path("/Volumes")
    for vol in volumes.iterdir():
        if vol.is_dir() and (vol / "iPod_Control").exists():
            return str(vol)
    raise MountError("iPod not found in /Volumes/. Make sure it is connected.")


def _mount_linux(device: str = "") -> str:
    """Mount iPod on Linux."""
    mount_point = "/media/ipod"

    # Already mounted?
    mp = Path(mount_point)
    if mp.exists() and mp.is_mount():
        return mount_point

    if not device:
        device = _find_ipod_block_device()
        if not device:
            raise MountError(
                "iPod connected but block device not found.\n"
                "Try: lsblk -o NAME,VENDOR,MODEL"
            )

    Path(mount_point).mkdir(parents=True, exist_ok=True)

    # Detect filesystem type via lsblk (blkid may not be installed)
    lsblk_fstype = subprocess.run(
        ["lsblk", "-o", "FSTYPE", "-n", device],
        capture_output=True, text=True, timeout=10,
    )
    fstype = lsblk_fstype.stdout.strip()

    if fstype == "hfsplus":
        # HFS+ (Mac-formatted iPod): requires sudo + force to get rw access.
        # Run with sudo — expects NOPASSWD entry in /etc/sudoers.d/ipod-mount.
        result = subprocess.run(
            ["sudo", "mount", "-t", "hfsplus", "-o", "rw,force,umask=000", device, mount_point],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            # Make mount point writable to all so daemon (non-root) can write
            subprocess.run(["sudo", "chmod", "777", mount_point], timeout=5)
            return mount_point
    else:
        # FAT32 or other: use pmount
        result = subprocess.run(
            ["pmount", "--umask=000", device, "ipod"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return mount_point

    raise MountError(f"Error mounting iPod ({fstype}): {result.stderr.strip()}")


def _find_ipod_block_device() -> str | None:
    """Find iPod block device on Linux.

    Uses lsblk with FSTYPE to find the correct partition — the Apple disk
    entry has the vendor/model, but we need the child partition with a real
    filesystem (hfsplus or vfat). Picking the first partition by name would
    select the wrong one on Mac-formatted iPods (3-partition layout).
    """
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,FSTYPE,VENDOR,MODEL,TRAN", "-n", "-l"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.splitlines()

        # Find disk name from the Apple vendor entry
        disk_name = None
        for line in lines:
            if "apple" in line.lower():
                parts = line.split()
                candidate = parts[0]
                # Must be a disk (no trailing digit), e.g. "sda"
                if not candidate[-1].isdigit():
                    disk_name = candidate
                    break

        if not disk_name:
            return None

        # Among the partitions of that disk, return the one with a real
        # filesystem (hfsplus preferred, then vfat). Skip empty FSTYPE lines.
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            if name.startswith(disk_name) and name != disk_name and name[-1].isdigit():
                fstype = parts[1] if len(parts) > 1 else ""
                if fstype in ("hfsplus", "vfat"):
                    return f"/dev/{name}"

        # Fallback: last numbered partition of the disk
        for line in reversed(lines):
            parts = line.split()
            if parts and parts[0].startswith(disk_name) and parts[0][-1].isdigit():
                return f"/dev/{parts[0]}"

    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
