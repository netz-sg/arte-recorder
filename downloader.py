#!/usr/bin/env python3
"""Download / record an ARTE stream to a file via ffmpeg (no re-encoding)."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from notifications import TelegramNotifier
from stream_finder import ArteStreamFinder


def sanitize_filename(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name)
    return safe.strip().replace(" ", "_") or "arte"


class ArteDownloader:
    def __init__(self, config: dict):
        self.config = config
        self.output_dir = Path(config["output_dir"]).resolve()
        self.metadata_dir = Path(config["metadata_dir"]).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = config["user_agent"]
        self.ffmpeg_extra = config.get("ffmpeg_extra_args", [])
        self.telegram = TelegramNotifier.from_config(config)

    def _build_cmd(self, video_url: str, audio_url: Optional[str],
                   output_path: Path, duration: Optional[str]) -> list:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
               *self.ffmpeg_extra, "-i", video_url]
        if audio_url:
            cmd.extend(["-i", audio_url])
        if duration:
            cmd.extend(["-t", str(duration)])
        if audio_url:
            cmd.extend(["-c", "copy", "-map", "0:v:0", "-map", "1:a:0"])
        else:
            cmd.extend(["-c", "copy", "-map", "0"])
        cmd.extend(["-f", "matroska", str(output_path)])
        return cmd

    def download_from_url(self, video_url: str, name: str,
                          duration: Optional[str] = None, audio_url: Optional[str] = None) -> Path:
        if not video_url:
            raise ValueError("No stream URL provided.")

        # If a page URL slipped in, resolve it first.
        if ".m3u8" not in urlparse(video_url).path:
            return self.download_from_page(video_url, name, duration=duration)

        now = datetime.now(timezone.utc)
        filename = f"{sanitize_filename(name)}_{now.strftime('%Y%m%d_%H%M%S')}.mkv"
        output_path = self.output_dir / filename

        cmd = self._build_cmd(video_url, audio_url, output_path, duration)
        print(f"[Downloader] {name}\n  video: {video_url}\n  audio: {audio_url or '(muxed)'}\n  -> {output_path}")
        self.telegram.notify_job_started("download", name, video_url)

        result = subprocess.run(cmd)
        if result.returncode != 0 and not (output_path.exists() and output_path.stat().st_size > 1024):
            msg = f"Download failed for {name}"
            self.telegram.notify_job_completed("download", name, None, msg)
            raise RuntimeError(msg)

        meta = {
            "name": name, "video_url": video_url, "audio_url": audio_url,
            "downloaded_at": now.isoformat(), "output_file": str(output_path),
        }
        (self.metadata_dir / f"{filename}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"[Downloader] Completed: {output_path}")
        self.telegram.notify_job_completed("download", name, str(output_path),
                                           f"Download complete: {output_path.name}")
        return output_path

    def download_from_page(self, page_url: str, name: str = None,
                           duration: Optional[str] = None) -> Path:
        finder = ArteStreamFinder(page_url, self.user_agent)
        streams = [s for s in finder.discover() if s.url]
        if not streams:
            raise ValueError(f"No ARTE streams found: {page_url}")
        target = streams[0]
        return self.download_from_url(target.url, name or target.name,
                                      duration=duration, audio_url=target.audio_url)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download/record an ARTE stream")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--url", help="ARTE page URL or direct m3u8")
    parser.add_argument("--name", help="Output filename base")
    parser.add_argument("-t", "--duration", help="Limit duration (e.g. 30, 00:01:00)")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    dl = ArteDownloader(config)
    url = args.url or config.get("page_url")
    if not url:
        parser.print_help()
        sys.exit(1)
    dl.download_from_page(url, args.name, duration=args.duration)


if __name__ == "__main__":
    main()
