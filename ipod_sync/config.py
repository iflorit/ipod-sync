"""Configuration management for ipod-sync."""

import json
import platform
from pathlib import Path
from dataclasses import dataclass, field, asdict

import yaml


def _default_music_dir() -> str:
    home = Path.home()
    if platform.system() == "Darwin":
        return str(home / "Music" / "ipod-sync")
    return str(home / "ipod-music")


CONFIG_DIR = Path.home() / ".config" / "ipod-sync"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
LIBRARY_INDEX = CONFIG_DIR / "library.json"
COOKIES_FILE = CONFIG_DIR / "cookies.txt"
PID_FILE = CONFIG_DIR / "daemon.pid"
LOG_FILE = CONFIG_DIR / "daemon.log"


@dataclass
class Config:
    apple_id: str = ""
    music_dir: str = field(default_factory=_default_music_dir)
    download_interval_hours: int = 12
    max_tracks_per_playlist: int = 100
    daemon_playlists: list = field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = yaml.safe_load(f) or {}
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(asdict(self), f, default_flow_style=False)

    @property
    def music_path(self) -> Path:
        return Path(self.music_dir)


def ensure_dirs() -> None:
    """Create all required directories."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_library_index() -> dict:
    """Load local library index (tracks already downloaded)."""
    if LIBRARY_INDEX.exists():
        with open(LIBRARY_INDEX) as f:
            return json.load(f)
    return {"tracks": {}, "playlists": {}, "last_sync": None}


def save_library_index(index: dict) -> None:
    """Save local library index."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIBRARY_INDEX, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
