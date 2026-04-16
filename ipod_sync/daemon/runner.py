"""Headless daemon: periodic Apple Music download + auto-sync on iPod connect."""

import logging
import threading

from ipod_sync.config import Config, COOKIES_FILE, LOG_FILE


class DaemonRunner:
    def __init__(self, config: Config, foreground: bool = False):
        self.config = config
        self._stop = threading.Event()
        self._sync_lock = threading.Lock()
        self._setup_logging(foreground)

    def _setup_logging(self, foreground: bool) -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(fmt)

        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(file_handler)

        if foreground:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(fmt)
            root.addHandler(console_handler)

        self._log = logging.getLogger("daemon")

    def run(self) -> None:
        """Start daemon loops. Blocks until SIGTERM/SIGINT."""
        import os
        import signal

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        interval = self.config.download_interval_hours
        playlists = self.config.daemon_playlists

        self._log.info(f"Daemon started (PID {os.getpid()})")
        self._log.info(f"Download interval: {interval}h | Playlists: {playlists or 'all'}")

        dl_thread = threading.Thread(target=self._download_loop, name="downloader", daemon=True)
        watch_thread = threading.Thread(target=self._ipod_loop, name="watcher", daemon=True)

        dl_thread.start()
        watch_thread.start()

        self._stop.wait()
        self._log.info("Daemon stopped.")

    def _handle_signal(self, signum, frame) -> None:
        self._log.info(f"Signal {signum} received, stopping...")
        self._stop.set()

    def _download_loop(self) -> None:
        """Download configured playlists every N hours."""
        from pathlib import Path

        from ipod_sync.download.downloader import download_track, verify_track
        from ipod_sync.download.library import (
            AppleMusicClient,
            _track_key,
            compute_diff,
            mark_downloaded,
            save_playlist,
        )

        interval_secs = self.config.download_interval_hours * 3600

        while not self._stop.is_set():
            self._log.info("Starting scheduled download...")
            try:
                cookies = str(COOKIES_FILE)
                if not Path(cookies).exists():
                    self._log.warning("Cookies file not found — skipping download")
                    self._stop.wait(timeout=interval_secs)
                    continue

                am = AppleMusicClient(cookies)
                configured = self.config.daemon_playlists

                if configured:
                    all_pls = am.list_playlists()
                    pl_map = {p["name"]: p for p in all_pls}
                    targets = []
                    for name in configured:
                        if name in pl_map:
                            targets.append(pl_map[name])
                        else:
                            self._log.warning(f"Playlist not found: {name!r}")
                else:
                    targets = am.list_playlists()

                total = 0
                for pl in targets:
                    remote = am.get_playlist_tracks(pl["id"], limit=200)
                    to_download, _ = compute_diff(remote)
                    for track in to_download:
                        try:
                            path = download_track(self.config, track, cookies)
                            if verify_track(path):
                                mark_downloaded(track, path)
                                total += 1
                        except Exception as e:
                            self._log.error(f"Download failed — {track.get('title')}: {e}")
                    all_keys = [_track_key(t["artist"], t["album"], t["title"]) for t in remote]
                    save_playlist(pl["name"], all_keys)

                am.close()
                self._log.info(f"Download complete: {total} new track(s)")

            except Exception as e:
                self._log.error(f"Download loop error: {e}")

            self._stop.wait(timeout=interval_secs)

    def _ipod_loop(self) -> None:
        """Poll for iPod connection; sync and eject when detected."""
        from ipod_sync.ipod.mount import unmount_ipod
        from ipod_sync.ipod.sync import sync_to_ipod
        from ipod_sync.ipod.watcher import wait_for_disconnect, wait_for_ipod

        self._log.info("iPod watcher started — polling for connection...")

        while not self._stop.is_set():
            mount = wait_for_ipod(stop_event=self._stop)
            if mount is None:
                break  # stop_event was set

            self._log.info(f"iPod connected at {mount}")

            with self._sync_lock:
                try:
                    self._log.info("Sync starting...")
                    added, removed = sync_to_ipod(mount, self.config)
                    self._log.info(f"Sync complete: +{added} -{removed}")
                except Exception as e:
                    self._log.error(f"Sync failed: {e}")
                    # Still try to eject even on failure
                    pass

                try:
                    unmount_ipod(mount)
                    self._log.info("iPod ejected — safe to disconnect.")
                except Exception as e:
                    self._log.warning(f"Eject failed: {e}")

            # Wait until the iPod physically disconnects before polling again
            wait_for_disconnect(mount, stop_event=self._stop)
            self._log.info("iPod disconnected — watching for next connection...")
