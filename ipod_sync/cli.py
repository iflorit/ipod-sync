"""ipod-sync CLI: Apple Music library sync to iPod Classic."""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ipod_sync.config import Config, CONFIG_DIR, ensure_dirs, load_library_index

console = Console()

COOKIES_PATH = str(CONFIG_DIR / "cookies.txt")


@click.group()
@click.version_option(package_name="ipod-sync")
def cli():
    """ipod-sync: Apple Music → iPod Classic."""
    pass


@cli.command()
@click.option("--limit", "-n", default=50, help="Max tracks to fetch (per playlist with --all-playlists)")
@click.option("--playlist", "-p", default=None, help="Playlist name to download")
@click.option("--all-playlists", is_flag=True, help="Download all playlists")
@click.option("--list-playlists", is_flag=True, help="List available playlists")
def download(limit, playlist, all_playlists, list_playlists):
    """Download tracks from Apple Music library."""
    from ipod_sync.download.library import AppleMusicClient
    from ipod_sync.download.downloader import download_track, verify_track

    ensure_dirs()
    config = Config.load()

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
            remote = am.get_playlist_tracks(p["id"], limit=limit)
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


if __name__ == "__main__":
    cli()
