# ipod-sync

Download your Apple Music library and sync it to an iPod Classic — no iTunes, no Docker, no proprietary hardware.

---

## The story

I still use an iPod Classic. It's a 5th gen, and it's perfect for what it does: battery life measured in days, no distractions, and a scroll wheel that feels better than any touchscreen for skipping songs while walking.

The problem: Apple removed iTunes from macOS years ago. Music.app doesn't sync to classic iPods. The only official path is an old Windows iTunes install or a virtual machine — neither of which I wanted to maintain.

So I built this.

**ipod-sync** is a CLI (eventually a web UI) that:
1. Fetches your Apple Music library via the internal AMP API
2. Downloads tracks using [gamdl](https://github.com/nicosemp/gamdl)
3. Syncs everything to the iPod via [libgpod](https://github.com/fadingred/libgpod), writing a correct iTunesDB

It runs on a Mac or any Raspberry Pi. The long-term goal is a small Pi device that sits on your desk: plug in the iPod, it syncs automatically, then ejects.

---

## What works today

- Download tracks and playlists from Apple Music (requires paid subscription + cookies)
- Sync to iPod Classic with correct metadata (title, artist, album, genre, duration)
- Playlist support: named playlists appear on the iPod
- Automatic iPod detection (macOS + Linux)
- Works on Mac (Apple Silicon / Intel) and Raspberry Pi

## What's coming

- **Album artwork** — the code is ready, blocked on recompiling libgpod with gdk-pixbuf
- **Web UI** with iTunes aesthetic — manage cookies, select playlists, monitor sync progress
- **Auto-sync daemon** — detects iPod on connect, syncs, then ejects automatically
- **Pi support** — run the whole thing headless on a Pi

---

## Requirements

- Python 3.10+
- [gamdl](https://github.com/nicosemp/gamdl) — `pip install gamdl`
- [libgpod](https://github.com/fadingred/libgpod) 0.8.x
- ffprobe (part of ffmpeg) — for track verification
- Apple Music subscription + cookies exported from `music.apple.com`

---

## Installation

### macOS

```bash
git clone https://github.com/iflorit/ipod-sync
cd ipod-sync
bash scripts/install-mac.sh
```

### Raspberry Pi

```bash
git clone https://github.com/iflorit/ipod-sync
cd ipod-sync
bash scripts/install-pi.sh
```

### Manual

```bash
pip install gamdl
pip install -e .
```

You also need libgpod installed. On Debian/Ubuntu/Raspberry Pi OS:

```bash
sudo apt install libgpod-dev libgpod-common
```

On macOS, libgpod must be compiled from source — see [the build notes](tasks/lessons.md) for the exact flags needed on ARM64.

---

## Setup: cookies

ipod-sync authenticates with Apple Music using your browser cookies. You do **not** need to provide your Apple ID password.

1. Log in to [music.apple.com](https://music.apple.com) in your browser
2. Install a cookie export extension (e.g. *Get cookies.txt LOCALLY* for Chrome)
3. Export cookies from `music.apple.com` in **Netscape format**
4. Save the file to `~/.config/ipod-sync/cookies.txt`

The developer token is extracted automatically from the Apple Music web player JS — no manual steps needed.

Cookies expire roughly every few months. When you get a `401 Sesion expirada` error, re-export them.

---

## Usage

```bash
# List your Apple Music playlists
ipod-sync playlists

# Download a playlist (up to 100 tracks)
ipod-sync download --playlist "Canciones favoritas" --limit 100

# Download from your full library
ipod-sync download --limit 50

# Sync to connected iPod
ipod-sync sync

# Check status
ipod-sync status
```

---

## How it works

```
Apple Music web ──► AMP API (httpx + cookies)
                          │
                   library.json index
                   (~/.config/ipod-sync/)
                          │
                    gamdl download
                          │
                  ~/Music/ipod-sync/*.m4a
                          │
                 mutagen (read m4a tags)
                          │
                  libgpod via ctypes
                          │
             iPod_Control/iTunes/iTunesDB
             iPod_Control/Music/F*/*.m4a
```

The sync always writes a fresh iTunesDB (clean slate). This is intentional — the iPod Classic 5th gen firmware is strict about the DB format, and incremental updates are fragile.

Key constraint: the iPod Classic 5th gen expects `mhit` headers of exactly 156 bytes and specific field offsets in the `Itdb_Track` struct. See `tasks/lessons.md` for the full list of firmware quirks that took a while to figure out.

---

## macOS permissions

On macOS, the process running `ipod-sync sync` needs **Full Disk Access** to read and write `/Volumes/<iPod>`. Without it, operations fail silently.

Add your terminal or Python binary in: **System Settings → Privacy & Security → Full Disk Access**

---

## License

[GNU Affero General Public License v3.0](LICENSE)

You can use, modify, and distribute this software freely under the terms of the AGPL-3.0. If you want to use it in a closed-source product or service without publishing your source code, contact me for a commercial license.
