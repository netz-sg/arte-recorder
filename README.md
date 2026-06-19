# ARTE Recorder

Record live streams and download VODs from **ARTE / ARTE Concert** in the best
available quality — with sound. No browser automation, no Playwright: ARTE
exposes a clean public player config API, so a single request resolves the HLS
master playlist. The tool always picks the highest-quality video variant and
records it together with ARTE's separate audio rendition.

Works on macOS, Linux, and Proxmox/servers. Web UI + CLI + background daemon.

## Features

- **API-based discovery** — resolves any `arte.tv` video URL via ARTE's player API
- **Best quality + sound** — highest video variant + separate audio track, muxed by ffmpeg (`-c copy`, no re-encoding)
- **Live recording** with automatic reconnect if the stream drops
- **VOD download** for on-demand videos
- **Modern web UI** — dark theme, live progress, archive browser
- **Telegram notifications** (optional) when jobs start/finish

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt   # requests + flask
cp config.example.json config.json

python3 webui.py --port 5050      # open http://localhost:5050
```

ffmpeg must be installed and on `PATH` (`brew install ffmpeg` / `apt install ffmpeg`).

## Web UI

1. Paste an ARTE URL, e.g. `https://www.arte.tv/de/videos/132146-001-A/southside-festival-2026/`
2. **Stream finden** — shows metadata + the resolved stream
3. **Aufnehmen** (live) or **Download** (VOD)
4. Jobs run in the background; finished files appear in **Archiv**

## CLI

```bash
# Download / record (page URL or direct .m3u8)
python3 downloader.py --url "https://www.arte.tv/de/videos/132146-001-A/..." --name "Southside_2026"

# Test the first 30 seconds
python3 downloader.py --url "..." --name "Test" -t 30

# Inspect what gets resolved
python3 stream_finder.py "https://www.arte.tv/de/videos/132146-001-A/..."

# Show metadata
python3 metadata.py "https://www.arte.tv/de/videos/132146-001-A/..."
```

## Background daemon

Records every stream found on the configured `page_url`, reconnecting on drops:

```bash
python3 recorder.py            # uses config.json
```

## Configuration (`config.json`)

| Key | Meaning |
|-----|---------|
| `page_url` | Default ARTE URL |
| `output_dir` / `temp_dir` / `metadata_dir` | Storage locations |
| `record_format` | `mp4` (default) or `mkv` |
| `reconnect_delay_seconds`, `max_reconnects`, `record_max_outage_seconds` | Live reconnect behaviour |
| `ffmpeg_extra_args` | Extra ffmpeg input flags (reconnect/timeout) |
| `telegram.bot_token` / `chat_id` / `thread_id` | Optional notifications |

> `config.json` and any secrets are git-ignored. Copy `config.example.json` and
> fill in your own values. Never commit real tokens.

## How it works

1. Extract the ARTE program id (e.g. `132146-001-A`) from the URL
2. Query `api.arte.tv/api/player/v2/config/<lang>/<id>` → HLS master playlist + metadata
3. Parse the master: pick the highest-bandwidth video variant + the `EXT-X-MEDIA` audio rendition
4. Record with ffmpeg (`-map 0:v:0 -map 1:a:0 -c copy`) into the configured container

## Where recordings are saved

- Finished files: `recordings/` (`.mp4` by default; `.mkv` if configured)
- Temp/part files while recording: `temp/`
- Per-recording metadata: `metadata/*.json`
