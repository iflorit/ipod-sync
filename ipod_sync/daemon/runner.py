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

        root = logging.getLogger()
        root.setLevel(logging.INFO)

        if foreground:
            # systemd captures stdout → daemon.log; don't double-write via FileHandler
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(fmt)
            root.addHandler(console_handler)
        else:
            file_handler = logging.FileHandler(LOG_FILE)
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)

        logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per API call otherwise
        self._log = logging.getLogger("daemon")

    def run(self) -> None:
        """Start daemon loops. Blocks until SIGTERM/SIGINT."""
        import os
        import signal

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        playlists = self.config.daemon_playlists

        self._log.info(f"Daemon started (PID {os.getpid()})")
        h, m = self.config.download_time
        self._log.info(f"Download time: {h:02d}:{m:02d} daily | Playlists: {playlists or 'all'}")

        threads = [
            threading.Thread(target=self._download_loop, name="downloader", daemon=True),
            threading.Thread(target=self._ipod_loop, name="watcher", daemon=True),
            threading.Thread(target=self._portal_loop, name="portal", daemon=True),
            threading.Thread(target=self._session_loop, name="session", daemon=True),
            threading.Thread(target=self._wifi_loop, name="wifi", daemon=True),
        ]
        for t in threads:
            t.start()

        self._stop.wait()
        self._log.info("Daemon stopped.")

    def _handle_signal(self, signum, frame) -> None:
        self._log.info(f"Signal {signum} received, stopping...")
        self._stop.set()

    def _secs_until_next_run(self) -> float:
        """Seconds until next scheduled download (daily at download_time)."""
        from datetime import datetime, timedelta
        hour, minute = self.config.download_time
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _download_loop(self) -> None:
        """Download configured playlists once a day at the configured time."""
        from pathlib import Path

        from ipod_sync.download.downloader import download_track, verify_track
        from ipod_sync.download.library import (
            AppleMusicClient,
            _track_key,
            compute_diff,
            mark_downloaded,
            save_playlist,
        )

        while not self._stop.is_set():
            self._log.info("Starting scheduled download...")
            try:
                cookies = str(COOKIES_FILE)
                if not Path(cookies).exists():
                    raise FileNotFoundError(f"Cookies file not found: {cookies} — skipping download")

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
                    try:
                        remote = am.get_playlist_tracks(pl["id"], limit=self.config.max_tracks_per_playlist)
                    except Exception as e:
                        self._log.warning(f"Skipping playlist {pl['name']!r}: {e}")
                        continue
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
                if "Session expired" in str(e) or "media-user-token" in str(e):
                    from ipod_sync.web import session
                    session.record_error(str(e))
                    self._notify("La sesión de Apple Music del iPod ha caducado. Acerca el iPhone y renuévala.")

            wait = self._secs_until_next_run()
            h, m = self.config.download_time
            self._log.info(f"Next download scheduled at {h:02d}:{m:02d} (in {wait/3600:.1f}h)")
            self._stop.wait(timeout=wait)

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

    # --- portal / session / wifi -------------------------------------------------

    def _daemon_state(self) -> dict:
        from ipod_sync.config import load_library_index
        from ipod_sync.ipod.detect import detect_ipod
        h, m = self.config.download_time
        mount = detect_ipod()
        return {
            "ipod": mount if mount and mount != "NOT_MOUNTED" else None,
            "tracks": len(load_library_index().get("tracks", {})),
            "download_time": f"{h:02d}:{m:02d}",
        }

    def _portal_loop(self) -> None:
        from ipod_sync.web.portal import serve
        try:
            serve(self.config.portal_port, daemon_state=self._daemon_state, stop_event=self._stop)
        except Exception as e:
            self._log.error(f"Portal failed: {e}")

    def _notify(self, text: str) -> None:
        """Best-effort push via configured webhook (GET url?text=...)."""
        if not self.config.notify_url:
            return
        try:
            import httpx
            httpx.get(self.config.notify_url, params={"text": text}, timeout=15)
            self._log.info(f"Notified: {text}")
        except Exception as e:
            self._log.warning(f"Notify failed: {e}")

    def _iphone_in_range(self) -> bool:
        """Ping the paired iPhone over Bluetooth (l2ping needs root; the service runs as root)."""
        import subprocess
        mac = self.config.proximity_bt_mac
        if not mac:
            return False
        try:
            r = subprocess.run(["l2ping", "-c", "1", "-t", "3", mac],
                               capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _session_loop(self) -> None:
        """Warn before the Apple Music token expires; nudge when the iPhone is nearby."""
        from ipod_sync.web import session
        last_nudge = 0.0
        last_daily = 0.0
        import time
        while not self._stop.is_set():
            now = time.time()
            st = session.status()
            if now - last_daily > 86400:
                last_daily = now
                if st["level"] == "ok":
                    self._log.info(f"Apple Music session OK — {st['days_left']} days left")
                else:
                    self._log.warning(f"Apple Music session {st['level']} (days_left={st['days_left']})")
            if st["level"] != "ok" and now - last_nudge > 6 * 3600 and self._iphone_in_range():
                last_nudge = now
                self._log.info("iPhone in Bluetooth range — sending renewal nudge")
                self._notify("Tu iPhone está cerca del iPod: abre http://%s:%d para renovar la sesión de Apple Music."
                             % (__import__("ipod_sync.web.portal", fromlist=["portal_host"]).portal_host(),
                                self.config.portal_port))
            self._stop.wait(timeout=60 if self.config.proximity_bt_mac else 3600)

    def _wifi_loop(self) -> None:
        """Provisioning fallback.

        Raise the setup hotspot when there is no connectivity at all (no default
        route) and either the device was never provisioned (no known Wi-Fi) or it
        has been offline for 5 min. The hotspot holds wlan0, so it is dropped again
        after 10 min to let NetworkManager retry the known networks; the cycle
        repeats until one side succeeds.
        """
        import subprocess
        import time
        from ipod_sync.web import wifi

        if not self.config.hotspot_when_offline:
            return

        def has_default_route() -> bool:
            r = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True)
            return bool(r.stdout.strip())

        offline_since = None
        hotspot_since = None
        while not self._stop.is_set():
            try:
                mode = wifi.status().get("mode")
                if mode == "hotspot":
                    hotspot_since = hotspot_since or time.time()
                    if time.time() - hotspot_since > 600:
                        self._log.info("Hotspot timeout — dropping it to retry known Wi-Fi")
                        wifi.hotspot_stop()
                        hotspot_since = None
                        offline_since = None
                elif has_default_route():
                    offline_since = None
                    hotspot_since = None
                else:
                    offline_since = offline_since or time.time()
                    never_provisioned = not wifi.known_networks()
                    if never_provisioned or time.time() - offline_since > 300:
                        wifi.hotspot_start()
                        hotspot_since = time.time()
                        self._log.info(f"No connectivity — hotspot {wifi.HOTSPOT_SSID!r} raised; "
                                       f"portal at http://{wifi.HOTSPOT_IP}:{self.config.portal_port}")
            except Exception as e:
                self._log.error(f"Wi-Fi loop error: {e}")
            self._stop.wait(timeout=15)
