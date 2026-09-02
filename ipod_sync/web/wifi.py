"""Wi-Fi provisioning via NetworkManager (nmcli).

Two modes:
- station: connected to a known network (normal operation)
- hotspot: no known network reachable → the Pi raises its own AP so the iPhone
  can join it and configure Wi-Fi through the captive portal.
"""

import logging
import subprocess

logger = logging.getLogger("wifi")

HOTSPOT_CON = "ipod-setup"
HOTSPOT_SSID = "iPod-Setup"
HOTSPOT_IP = "10.42.0.1"
IFACE = "wlan0"


class WifiError(Exception):
    pass


def _nmcli(*args: str, timeout: int = 30) -> str:
    r = subprocess.run(["nmcli", "-t", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise WifiError(r.stderr.strip() or r.stdout.strip() or f"nmcli {' '.join(args)} failed")
    return r.stdout


def status() -> dict:
    """Current wlan0 state: mode, ssid, ip."""
    try:
        out = _nmcli("-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", IFACE)
    except (WifiError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"mode": "unknown", "error": str(e)}
    fields = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
    con = fields.get("GENERAL.CONNECTION", "")
    ip = fields.get("IP4.ADDRESS[1]", "").split("/")[0]
    connected = fields.get("GENERAL.STATE", "").startswith("100")
    if con == HOTSPOT_CON:
        mode = "hotspot"
    elif connected:
        mode = "station"
    else:
        mode = "disconnected"
    ssid = con
    if con and mode == "station":
        try:
            out = _nmcli("-f", "802-11-wireless.ssid", "con", "show", con)
            ssid = out.split(":", 1)[1].strip() or con
        except (WifiError, IndexError):
            pass
    return {"mode": mode, "connection": ssid, "ip": ip}


def scan() -> list[dict]:
    """Visible networks, strongest first, deduplicated by SSID."""
    out = _nmcli("-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes", timeout=45)
    seen: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.replace("\\:", ":").rsplit(":", 2)
        if len(parts) != 3 or not parts[0]:
            continue
        ssid, signal, sec = parts
        entry = {"ssid": ssid, "signal": int(signal or 0), "secured": bool(sec.strip())}
        if ssid not in seen or entry["signal"] > seen[ssid]["signal"]:
            seen[ssid] = entry
    return sorted(seen.values(), key=lambda e: -e["signal"])


def connect(ssid: str, password: str) -> dict:
    """Join a network. Persists it as a NetworkManager connection (autoconnect)."""
    if not ssid:
        raise WifiError("SSID vacío")
    # Drop a previous profile with the same name so a changed password wins.
    subprocess.run(["nmcli", "con", "delete", ssid], capture_output=True, text=True)
    args = ["dev", "wifi", "connect", ssid, "ifname", IFACE]
    if password:
        args += ["password", password]
    _nmcli(*args, timeout=60)
    logger.info(f"Wi-Fi connected to {ssid!r}")
    return status()


def hotspot_start() -> dict:
    """Raise the setup AP (open network, captive portal at HOTSPOT_IP)."""
    if status().get("mode") == "hotspot":
        return status()
    subprocess.run(["nmcli", "con", "delete", HOTSPOT_CON], capture_output=True, text=True)
    _nmcli("con", "add", "type", "wifi", "ifname", IFACE, "con-name", HOTSPOT_CON,
           "autoconnect", "no", "ssid", HOTSPOT_SSID,
           "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
           "ipv4.method", "shared", "ipv4.addresses", f"{HOTSPOT_IP}/24")
    _nmcli("con", "up", HOTSPOT_CON, timeout=60)
    logger.info(f"Hotspot {HOTSPOT_SSID!r} up at {HOTSPOT_IP}")
    return status()


def hotspot_stop() -> None:
    subprocess.run(["nmcli", "con", "down", HOTSPOT_CON], capture_output=True, text=True)


def known_networks() -> list[str]:
    out = _nmcli("-f", "NAME,TYPE", "con", "show")
    return [line.split(":")[0] for line in out.splitlines()
            if line.endswith("802-11-wireless") and not line.startswith(HOTSPOT_CON)]
