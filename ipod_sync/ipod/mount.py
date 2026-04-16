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
        # Try pumount first (matches pmount)
        result = subprocess.run(
            ["pumount", mount_point],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            # Fallback to udisksctl
            result = subprocess.run(
                ["udisksctl", "unmount", "-b", mount_point],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
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

    # Detect filesystem type
    blkid = subprocess.run(
        ["blkid", "-o", "value", "-s", "TYPE", device],
        capture_output=True, text=True, timeout=10,
    )
    fstype = blkid.stdout.strip()

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
    """Find iPod block device on Linux."""
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,VENDOR,MODEL,TRAN", "-n", "-l"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "apple" in line.lower():
                # Return the partition (usually sda1 or sdb1)
                device_name = parts[0]
                partition = f"/dev/{device_name}"
                # Check if it's a partition (ends with number)
                if device_name[-1].isdigit():
                    return partition
                # Otherwise look for partitions of this device
                for line2 in result.stdout.splitlines():
                    if line2.strip().startswith(device_name) and line2.strip() != device_name:
                        return f"/dev/{line2.split()[0]}"
                return f"/dev/{device_name}1"  # Guess first partition
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
