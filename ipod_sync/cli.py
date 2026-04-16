"""ipod-sync CLI: Apple Music library sync to iPod Classic."""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ipod_sync.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    COOKIES_FILE,
    PID_FILE,
    LOG_FILE,
    Config,
    ensure_dirs,
    load_library_index,
)

console = Console()

COOKIES_PATH = str(COOKIES_FILE)

_CONFIG_KEYS = {
    "interval":  ("download_interval_hours", int),
    "limit":     ("max_tracks_per_playlist",  int),
    "playlists": ("daemon_playlists",         lambda v: [s.strip() for s in v.split(",") if s.strip()] if v.strip() else []),
    "music-dir": ("music_dir",                str),
}


@click.group()
@click.version_option(package_name="ipod-sync")
def cli():
    """ipod-sync: Apple Music → iPod Classic."""
    pass


@cli.group("config")
def config_group():
    """View and edit configuration."""
    pass


@config_group.command("show")
def config_show():
    """Show current configuration."""
    config = Config.load()
    table = Table(title=f"Configuration ({CONFIG_FILE})")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_column("CLI key", style="dim")
    rows = [
        ("download_interval_hours", str(config.download_interval_hours), "interval"),
        ("max_tracks_per_playlist", str(config.max_tracks_per_playlist), "limit"),
        ("daemon_playlists",        ", ".join(config.daemon_playlists) or "[dim]all[/]", "playlists"),
        ("music_dir",               config.music_dir, "music-dir"),
    ]
    for field, value, key in rows:
        table.add_row(field, value, key)
    console.print(table)


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """Set a configuration value.

    Keys: interval, limit, playlists, music-dir

    Examples:
      ipod-sync config set interval 12
      ipod-sync config set limit 200
      ipod-sync config set playlists "Running, Workout"
      ipod-sync config set music-dir /data/music
    """
    if key not in _CONFIG_KEYS:
        valid = ", ".join(_CONFIG_KEYS)
        console.print(f"[red]Unknown key '{key}'.[/] Valid keys: {valid}")
        sys.exit(1)

    field_name, coerce = _CONFIG_KEYS[key]
    try:
        coerced = coerce(value)
    except (ValueError, TypeError) as e:
        console.print(f"[red]Invalid value for '{key}': {e}[/]")
        sys.exit(1)

    ensure_dirs()
    config = Config.load()
    setattr(config, field_name, coerced)
    config.save()
    display = ", ".join(coerced) if isinstance(coerced, list) else str(coerced)
    console.print(f"[green]{key}[/] = {display}")


@cli.command()
@click.option("--limit", "-n", default=None, type=int, help="Max tracks to fetch (per playlist with --all-playlists)")
@click.option("--playlist", "-p", default=None, help="Playlist name to download")
@click.option("--all-playlists", is_flag=True, help="Download all playlists")
@click.option("--list-playlists", is_flag=True, help="List available playlists")
def download(limit, playlist, all_playlists, list_playlists):
    """Download tracks from Apple Music library."""
    from ipod_sync.download.library import AppleMusicClient
    from ipod_sync.download.downloader import download_track, verify_track

    ensure_dirs()
    config = Config.load()

    if limit is None:
        limit = config.max_tracks_per_playlist

    if not Path(COOKIES_PATH).exists():
        console.print(f"[red]Cookies file not found at {COOKIES_PATH}[/]")
        console.print(f"Edit the file and replace REPLACE_WITH_YOUR_TOKEN with your media-user-token.")
        sys.exit(1)

    if playlist and all_playlists:
        console.print("[red]--playlist and --all-playlists are mutually exclusive.[/]")
        sys.exit(1)

    console.print("Connecting to Apple Music...")
    am = AppleMusicClient(COOKIES_PATH)

    # List playlists mode
    if list_playlists:
        pls = am.list_playlists()
        table = Table(title="Playlists")
        table.add_column("Name")
        table.add_column("ID", style="dim")
        for p in pls:
            table.add_row(p["name"], p["id"])
        console.print(table)
        am.close()
        return

    # All playlists mode
    if all_playlists:
        pls = am.list_playlists()
        console.print(f"  Found {len(pls)} playlists")
        total_ok = 0
        total_errors = 0
        for idx, p in enumerate(pls, 1):
            console.print(f"\n[bold][{idx}/{len(pls)}] {p['name']}[/]")
            try:
                remote = am.get_playlist_tracks(p["id"], limit=limit)
            except Exception as e:
                console.print(f"  [red]Skipped: {e}[/]")
                total_errors += 1
                continue
            ok, errors = _download_tracks(config, remote, p["name"])
            total_ok += ok
            total_errors += errors
        am.close()
        console.print(f"\n[bold green]{total_ok} tracks downloaded in total.[/]" +
                      (f" [red]{total_errors} errors.[/]" if total_errors else ""))
        return

    # Single playlist or full library
    playlist_name = None
    if playlist:
        p = am.find_playlist(playlist)
        if not p:
            console.print(f"[red]Playlist '{playlist}' not found.[/]")
            console.print("Use --list-playlists to see available playlists.")
            am.close()
            sys.exit(1)
        playlist_name = p["name"]
        console.print(f"  Playlist: [bold]{playlist_name}[/]")
        remote = am.get_playlist_tracks(p["id"], limit=limit)
    else:
        console.print(f"  Fetching up to {limit} tracks from library...")
        remote = am.get_library_songs(limit=limit)

    am.close()
    _download_tracks(config, remote, playlist_name)


def _download_tracks(config, remote: list, playlist_name: str | None) -> tuple[int, int]:
    """Download a list of tracks and persist playlist index. Returns (ok, errors)."""
    from ipod_sync.download.library import compute_diff, mark_downloaded, save_playlist, _track_key
    from ipod_sync.download.downloader import download_track, verify_track

    console.print(f"  Found: {len(remote)} tracks")

    to_download, _ = compute_diff(remote)
    console.print(f"  New: {len(to_download)}")

    ok = 0
    errors = 0

    if not to_download:
        console.print("[green]Already up to date.[/]")
    else:
        for i, track in enumerate(to_download, 1):
            label = f"{track['artist']} - {track['title']}"
            try:
                path = download_track(config, track, COOKIES_PATH)
                if verify_track(path):
                    mark_downloaded(track, path)
                    console.print(f"  [{i}/{len(to_download)}] {label} [green]OK[/]")
                    ok += 1
                else:
                    console.print(f"  [{i}/{len(to_download)}] {label} [red]verification failed[/]")
                    errors += 1
            except Exception as e:
                console.print(f"  [{i}/{len(to_download)}] {label} [red]{e}[/]")
                errors += 1

        console.print(f"  [bold green]{ok} downloaded.[/]" + (f" [red]{errors} errors.[/]" if errors else ""))

    if playlist_name:
        all_keys = [_track_key(t["artist"], t["album"], t["title"]) for t in remote]
        save_playlist(playlist_name, all_keys)
        console.print(f"  Playlist '[bold]{playlist_name}[/]' saved ({len(all_keys)} tracks)")

    return ok, errors


@cli.command()
def playlists():
    """List your Apple Music playlists."""
    from ipod_sync.download.library import AppleMusicClient

    ensure_dirs()
    if not Path(COOKIES_PATH).exists():
        console.print(f"[red]Cookies file not found at {COOKIES_PATH}[/]")
        sys.exit(1)

    console.print("Connecting to Apple Music...")
    am = AppleMusicClient(COOKIES_PATH)
    pls = am.list_playlists()
    am.close()

    table = Table(title=f"Your playlists ({len(pls)})")
    table.add_column("Name")
    table.add_column("ID", style="dim")
    for p in pls:
        table.add_row(p["name"], p["id"])
    console.print(table)


@cli.command()
def sync():
    """Sync library to connected iPod Classic."""
    from ipod_sync.ipod.detect import detect_ipod
    from ipod_sync.ipod.mount import mount_ipod
    from ipod_sync.ipod.sync import sync_to_ipod

    config = Config.load()

    console.print("Detecting iPod...")
    mount_point = detect_ipod()

    if mount_point is None:
        console.print("[red]iPod not detected.[/] Plug it in via USB.")
        sys.exit(1)

    if mount_point == "NOT_MOUNTED":
        console.print("iPod connected but not mounted. Mounting...")
        mount_point = mount_ipod()

    console.print(f"  iPod at {mount_point}")

    try:
        import shutil
        usage = shutil.disk_usage(mount_point)
        console.print(f"  Space: {usage.free / (1024**3):.1f}GB free / {usage.total / (1024**3):.1f}GB total")
    except Exception:
        pass

    def on_progress(action, title, current, total):
        symbol = "+" if action == "add" else "-" if action == "remove" else "!"
        console.print(f"  {symbol} [{current}/{total}] {title}")

    console.print("Syncing...")
    added, removed = sync_to_ipod(mount_point, config, on_progress)
    console.print(f"\n[bold green]Sync complete:[/] +{added} / -{removed}. Safe to disconnect.")


@cli.command()
def status():
    """Show current status."""
    from ipod_sync.ipod.detect import detect_ipod

    config = Config.load()
    index = load_library_index()

    table = Table(title="ipod-sync status")
    table.add_column("", style="bold")
    table.add_column("")

    table.add_row("Cookies", "[green]OK[/]" if Path(COOKIES_PATH).exists() else "[red]not found[/]")

    tracks = index.get("tracks", {})
    playlists = index.get("playlists", {})
    table.add_row("Local tracks", str(len(tracks)))
    table.add_row("Playlists", str(len(playlists)))
    table.add_row("Last sync", index.get("last_sync", "[dim]never[/]") or "[dim]never[/]")
    table.add_row("Music directory", config.music_dir)

    ipod = detect_ipod()
    if ipod and ipod != "NOT_MOUNTED":
        table.add_row("iPod", f"[green]connected[/] at {ipod}")
    elif ipod == "NOT_MOUNTED":
        table.add_row("iPod", "[yellow]connected (not mounted)[/]")
    else:
        table.add_row("iPod", "[dim]not detected[/]")

    console.print(table)


@cli.command()
def setup():
    """Interactive first-time setup (works over SSH, no screen needed)."""
    console.print("[bold]ipod-sync setup[/]")
    console.print("Configure your preferences. Press Enter to accept defaults.\n")

    ensure_dirs()
    config = Config.load()

    interval = click.prompt(
        "Download interval (hours)",
        default=config.download_interval_hours,
        type=int,
    )
    config.download_interval_hours = interval

    music_dir = click.prompt("Music storage directory", default=config.music_dir)
    config.music_dir = music_dir

    console.print("\nWhich playlists should the daemon auto-download?")
    console.print("Comma-separated names, or leave blank to sync all playlists.")
    current = ", ".join(config.daemon_playlists) if config.daemon_playlists else ""
    pl_input = click.prompt(
        "Playlists",
        default=current if current else "",
        show_default=bool(current),
    )
    if pl_input.strip():
        config.daemon_playlists = [p.strip() for p in pl_input.split(",") if p.strip()]
    else:
        config.daemon_playlists = []

    config.save()
    console.print(f"\n[green]Config saved to {CONFIG_FILE}[/]")
    console.print(f"  Music directory:   {config.music_dir}")
    console.print(f"  Download interval: {config.download_interval_hours}h")
    console.print(f"  Playlists:         {', '.join(config.daemon_playlists) or 'all'}")

    if not COOKIES_FILE.exists():
        console.print(f"\n[yellow]Cookies not found at {COOKIES_FILE}[/]")
        console.print("Export media-user-token from music.apple.com and add it to that file.")
    else:
        console.print(f"\n[green]Cookies: {COOKIES_FILE}[/]")

    console.print("\nTo start the daemon:    ipod-sync daemon start")
    console.print("To run as a service:    see scripts/install-pi.sh")


@cli.group()
def daemon():
    """Manage the background sync daemon."""
    pass


@daemon.command("start")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (use with systemd)")
def daemon_start(foreground):
    """Start the daemon."""
    import os
    import subprocess

    # Check for stale / live PID file
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)  # 0 = existence check only
            console.print(f"[yellow]Daemon already running (PID {pid})[/]")
            return
        except (ProcessLookupError, ValueError):
            PID_FILE.unlink(missing_ok=True)

    ensure_dirs()

    if foreground:
        # Used by systemd: write PID here, block until done
        from ipod_sync.config import Config
        from ipod_sync.daemon.runner import DaemonRunner

        PID_FILE.write_text(str(os.getpid()))
        try:
            DaemonRunner(Config.load(), foreground=True).run()
        finally:
            PID_FILE.unlink(missing_ok=True)
    else:
        # Background via subprocess — avoids fork hazards with ctypes libraries
        proc = subprocess.Popen(
            [sys.executable, "-m", "ipod_sync.daemon"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(f"[green]Daemon started (PID {proc.pid})[/]")
        console.print(f"Logs: {LOG_FILE}")


@daemon.command("stop")
def daemon_stop():
    """Stop the running daemon."""
    import os
    import signal as sig

    if not PID_FILE.exists():
        console.print("[yellow]No daemon running (no PID file found)[/]")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        console.print("[red]Corrupted PID file removed[/]")
        return

    try:
        os.kill(pid, sig.SIGTERM)
        console.print(f"[green]Daemon stopped (PID {pid})[/]")
    except ProcessLookupError:
        console.print(f"[yellow]Process {pid} not found — cleaning PID file[/]")
        PID_FILE.unlink(missing_ok=True)


@daemon.command("status")
def daemon_status():
    """Show daemon status and recent log entries."""
    import os

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            console.print(f"[green]Running[/] (PID {pid})")
        except (ProcessLookupError, ValueError):
            console.print("[red]Not running[/] (stale PID file)")
    else:
        console.print("[dim]Not running[/]")

    if LOG_FILE.exists():
        console.print(f"\n[bold]Recent log[/] ({LOG_FILE}):")
        lines = LOG_FILE.read_text().splitlines()
        for line in lines[-20:]:
            console.print(f"  {line}")


if __name__ == "__main__":
    cli()
