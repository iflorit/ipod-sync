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
    raise MountError(f"Plataforma no soportada: {system}")


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
            raise MountError(f"Error expulsando iPod: {result.stderr}")
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
                raise MountError(f"Error desmontando iPod: {result.stderr}")


def _mount_macos() -> str:
    """On macOS, iPod auto-mounts. Find and return the path."""
    volumes = Path("/Volumes")
    for vol in volumes.iterdir():
        if vol.is_dir() and (vol / "iPod_Control").exists():
            return str(vol)
    raise MountError("iPod no encontrado en /Volumes/. Asegurate de que esta conectado.")


def _mount_linux(device: str = "") -> str:
    """Mount iPod on Linux using pmount."""
    mount_point = "/media/ipod"

    # Already mounted?
    mp = Path(mount_point)
    if mp.exists() and mp.is_mount():
        return mount_point

    if not device:
        # Auto-detect device block
        device = _find_ipod_block_device()
        if not device:
            raise MountError(
                "iPod conectado pero no se encuentra el dispositivo de bloque.\n"
                "Prueba: lsblk -o NAME,VENDOR,MODEL"
            )

    result = subprocess.run(
        ["pmount", "--umask=000", device, "ipod"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        # Fallback to udisksctl
        result = subprocess.run(
            ["udisksctl", "mount", "-b", device],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise MountError(f"Error montando iPod: {result.stderr}")
        # Parse mount point from udisksctl output
        for line in result.stdout.splitlines():
            if "Mounted" in line and "at" in line:
                return line.split("at")[-1].strip().rstrip(".")
        raise MountError("Montado pero no se pudo determinar el punto de montaje")

    return mount_point


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
