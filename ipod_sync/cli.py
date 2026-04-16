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
@click.option("--limit", "-n", default=50, help="Numero maximo de canciones")
@click.option("--playlist", "-p", default=None, help="Nombre de la playlist a descargar")
@click.option("--list-playlists", is_flag=True, help="Listar playlists disponibles")
def download(limit, playlist, list_playlists):
    """Download tracks from Apple Music library."""
    from ipod_sync.download.library import AppleMusicClient, compute_diff, mark_downloaded, save_playlist, _track_key
    from ipod_sync.download.downloader import download_track, verify_track

    ensure_dirs()
    config = Config.load()

    if not Path(COOKIES_PATH).exists():
        console.print(f"[red]Cookies no encontradas en {COOKIES_PATH}[/]")
        console.print("Exporta cookies de music.apple.com y colocalas ahi.")
        sys.exit(1)

    console.print("Conectando con Apple Music...")
    am = AppleMusicClient(COOKIES_PATH)

    # List playlists mode
    if list_playlists:
        playlists = am.list_playlists()
        table = Table(title="Playlists")
        table.add_column("Nombre")
        table.add_column("ID", style="dim")
        for p in playlists:
            table.add_row(p["name"], p["id"])
        console.print(table)
        am.close()
        return

    # Fetch tracks
    playlist_name = None
    if playlist:
        p = am.find_playlist(playlist)
        if not p:
            console.print(f"[red]Playlist '{playlist}' no encontrada.[/]")
            console.print("Usa --list-playlists para ver las disponibles.")
            am.close()
            sys.exit(1)
        playlist_name = p["name"]
        console.print(f"  Playlist: [bold]{playlist_name}[/]")
        remote = am.get_playlist_tracks(p["id"], limit=limit)
    else:
        console.print(f"  Descargando hasta {limit} canciones de la biblioteca...")
        remote = am.get_library_songs(limit=limit)

    am.close()
    console.print(f"  Encontradas: {len(remote)} canciones")

    # Diff with local
    to_download, _ = compute_diff(remote)
    console.print(f"  Nuevas: {len(to_download)}")

    if not to_download:
        console.print("[green]Todo al dia.[/]")
        # Still update the playlist index even if nothing new to download
        if playlist_name:
            all_keys = [_track_key(t["artist"], t["album"], t["title"]) for t in remote]
            save_playlist(playlist_name, all_keys)
        return

    # Download one by one
    ok = 0
    errors = 0
    for i, track in enumerate(to_download, 1):
        label = f"{track['artist']} - {track['title']}"
        try:
            path = download_track(config, track, COOKIES_PATH)
            if verify_track(path):
                mark_downloaded(track, path)
                console.print(f"  [{i}/{len(to_download)}] {label} [green]OK[/]")
                ok += 1
            else:
                console.print(f"  [{i}/{len(to_download)}] {label} [red]verificacion fallida[/]")
                errors += 1
        except Exception as e:
            console.print(f"  [{i}/{len(to_download)}] {label} [red]{e}[/]")
            errors += 1

    console.print(f"\n[bold green]{ok} descargadas.[/]" + (f" [red]{errors} errores.[/]" if errors else ""))

    # Persist playlist membership for all tracks in the playlist (not just newly downloaded)
    if playlist_name:
        all_keys = [_track_key(t["artist"], t["album"], t["title"]) for t in remote]
        save_playlist(playlist_name, all_keys)
        console.print(f"  Playlist '[bold]{playlist_name}[/]' guardada ({len(all_keys)} canciones)")


@cli.command()
def playlists():
    """List your Apple Music playlists."""
    from ipod_sync.download.library import AppleMusicClient

    ensure_dirs()
    if not Path(COOKIES_PATH).exists():
        console.print(f"[red]Cookies no encontradas en {COOKIES_PATH}[/]")
        sys.exit(1)

    console.print("Conectando con Apple Music...")
    am = AppleMusicClient(COOKIES_PATH)
    pls = am.list_playlists()
    am.close()

    table = Table(title=f"Tus playlists ({len(pls)})")
    table.add_column("Nombre")
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

    console.print("Detectando iPod...")
    mount_point = detect_ipod()

    if mount_point is None:
        console.print("[red]iPod no detectado.[/] Enchufalo por USB.")
        sys.exit(1)

    if mount_point == "NOT_MOUNTED":
        console.print("iPod conectado pero no montado. Montando...")
        mount_point = mount_ipod()

    console.print(f"  iPod en {mount_point}")

    try:
        import shutil
        usage = shutil.disk_usage(mount_point)
        console.print(f"  Espacio: {usage.free / (1024**3):.1f}GB libres / {usage.total / (1024**3):.1f}GB total")
    except Exception:
        pass

    def on_progress(action, title, current, total):
        symbol = "+" if action == "add" else "-" if action == "remove" else "!"
        console.print(f"  {symbol} [{current}/{total}] {title}")

    console.print("Sincronizando...")
    added, removed = sync_to_ipod(mount_point, config, on_progress)
    console.print(f"\n[bold green]Sync completado:[/] +{added} / -{removed}. Puedes desconectar.")


@cli.command()
def status():
    """Show current status."""
    from ipod_sync.ipod.detect import detect_ipod

    config = Config.load()
    index = load_library_index()

    table = Table(title="ipod-sync status")
    table.add_column("", style="bold")
    table.add_column("")

    table.add_row("Cookies", "[green]OK[/]" if Path(COOKIES_PATH).exists() else "[red]no encontradas[/]")

    tracks = index.get("tracks", {})
    table.add_row("Canciones locales", str(len(tracks)))
    table.add_row("Ultimo sync", index.get("last_sync", "[dim]nunca[/]") or "[dim]nunca[/]")
    table.add_row("Directorio musica", config.music_dir)

    ipod = detect_ipod()
    if ipod and ipod != "NOT_MOUNTED":
        table.add_row("iPod", f"[green]conectado[/] en {ipod}")
    elif ipod == "NOT_MOUNTED":
        table.add_row("iPod", "[yellow]conectado (no montado)[/]")
    else:
        table.add_row("iPod", "[dim]no detectado[/]")

    console.print(table)


if __name__ == "__main__":
    cli()
