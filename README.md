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
- Works on Mac (Apple Silicon / Intel) and any Raspberry Pi
- **Headless daemon** — runs on a Pi with no screen or keyboard. Polls Apple Music on a schedule, detects iPod on USB connect, syncs, and ejects automatically. Managed as a systemd service.

## What's coming

- **Album artwork** — the code is ready, blocked on recompiling libgpod with gdk-pixbuf
- **Web UI** with iTunes aesthetic — manage cookies, select playlists, monitor sync progress
- **Sync log written to the iPod's Notes** — after each sync, ipod-sync writes a plain-text note directly to the iPod. Open the Notes app on the iPod to see a reverse-chronological log of what was synced: which playlists and tracks were added or removed each day, and any errors (expired cookies, download failures). No app needed — the iPod itself shows you what happened.
- **Format transcoding** — convert FLAC, OGG, and other formats to AAC/ALAC before sync

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

ipod-sync authenticates with Apple Music using your browser session cookie. You do **not** need to provide your Apple ID password.

### 1. Create the cookies file

The installation scripts create the file automatically. If you're setting up manually:

```bash
mkdir -p ~/.config/ipod-sync
touch ~/.config/ipod-sync/cookies.txt
```

### 2. Cookie file format

`~/.config/ipod-sync/cookies.txt` must be in **Netscape format** — one cookie per line, fields separated by tabs:

```
# Netscape HTTP Cookie File
# https://curl.se/docs/http-cookies.html

.music.apple.com	TRUE	/	TRUE	1893456000	media-user-token	eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJBTVBfQVBJIiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjE4OTM0NTYwMDB9.REPLACE_WITH_YOUR_TOKEN
```

The only cookie ipod-sync needs is `media-user-token`. The other fields:

| Field | Value | Notes |
|---|---|---|
| domain | `.music.apple.com` | leading dot = all subdomains |
| flag | `TRUE` | subdomain match enabled |
| path | `/` | |
| secure | `TRUE` | HTTPS only |
| expiry | Unix timestamp | e.g. `1893456000` = year 2030 |
| name | `media-user-token` | must be exactly this |
| value | `eyJhbGci...` | your JWT token (long string) |

### 3. Get your token

1. Log in to [music.apple.com](https://music.apple.com) in your browser
2. Install a cookie export extension (e.g. *Get cookies.txt LOCALLY* for Chrome/Firefox)
3. Export cookies from `music.apple.com` in **Netscape format**
4. Copy the line containing `media-user-token` into `~/.config/ipod-sync/cookies.txt`

The developer token (for the Apple Music API) is extracted automatically from the web player — no manual steps needed for that.

Cookies expire roughly every few months. When you get a `Session expired` error, re-export the `media-user-token` line and replace the value in `cookies.txt`.

---

## Usage

```bash
# List your Apple Music playlists
ipod-sync playlists

# Download a single playlist (up to 100 tracks)
ipod-sync download --playlist "My Playlist" --limit 100

# Download all your playlists (up to 50 tracks each)
ipod-sync download --all-playlists --limit 50

# Download from your full library (no playlist grouping)
ipod-sync download --limit 50

# Sync to connected iPod
ipod-sync sync

# Check status
ipod-sync status
```

### Headless daemon (Raspberry Pi)

Configure once, leave running. The daemon polls Apple Music on a schedule and syncs the iPod automatically when it's plugged in.

```bash
# First-time configuration (works over SSH — no screen needed)
ipod-sync setup

# Start the daemon in the background
ipod-sync daemon start

# Check whether it's running and see recent log entries
ipod-sync daemon status

# Stop the daemon
ipod-sync daemon stop
```

On a Raspberry Pi installed via `scripts/install-pi.sh`, the daemon runs as a systemd service and starts automatically on boot:

```bash
sudo systemctl start ipod-sync
sudo systemctl status ipod-sync
journalctl -u ipod-sync -f   # follow logs
```

**What it does automatically:**
1. Every N hours (configurable, default 6): downloads new tracks from your configured playlists
2. Detects when the iPod is plugged in via USB
3. Syncs all tracks to the iPod
4. Ejects the iPod when done — safe to unplug

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
