#!/usr/bin/env python3
"""Background daemon: record an ARTE live stream, reconnecting on drops."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from notifications import TelegramNotifier
from stream_finder import ArteStreamFinder, StreamInfo
from metadata import ArteMetadata


def _sanitize(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name)
    return safe.strip().replace(" ", "_") or "arte"


class ArteRecorder:
    def __init__(self, config: dict):
        self.config = config
        self.output_dir = Path(config["output_dir"]).resolve()
        self.metadata_dir = Path(config["metadata_dir"]).resolve()
        self.temp_dir = Path(config["temp_dir"]).resolve()
        for d in (self.output_dir, self.metadata_dir, self.temp_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.page_url = config["page_url"]
        self.user_agent = config["user_agent"]
        self.check_interval = config.get("check_interval_seconds", 60)
        self.reconnect_delay = config.get("reconnect_delay_seconds", 10)
        self.max_reconnects = config.get("max_reconnects", 100)
        self.ffmpeg_extra = config.get("ffmpeg_extra_args", [])

        self.finder = ArteStreamFinder(self.page_url, self.user_agent)
        self.metadata = ArteMetadata(self.page_url, self.user_agent)
        self.telegram = TelegramNotifier.from_config(config)

        self.active: Dict[str, subprocess.Popen] = {}
        self.reconnects: Dict[str, int] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        signal.signal(signal.SIGTERM, self._sig)
        signal.signal(signal.SIGINT, self._sig)

    def _sig(self, *_):
        print("\n[Recorder] Stopping...")
        self.stop()

    def _build_cmd(self, stream: StreamInfo, output_path: Path) -> List[str]:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
               *self.ffmpeg_extra, "-i", stream.url]
        if stream.audio_url:
            cmd.extend(["-i", stream.audio_url, "-c", "copy",
                        "-map", "0:v:0", "-map", "1:a:0"])
        else:
            cmd.extend(["-c", "copy", "-map", "0"])
        cmd.extend(["-f", "matroska", str(output_path)])
        return cmd

    def start_recording(self, stream: StreamInfo) -> bool:
        key = stream.name
        with self._lock:
            if key in self.active or not stream.url:
                return False

        now = datetime.now(timezone.utc)
        filename = f"{_sanitize(stream.name)}_{now.strftime('%Y%m%d_%H%M%S')}.mkv"
        output_path = self.output_dir / filename
        temp_path = self.temp_dir / f"temp_{filename}"

        info = {
            "name": stream.name, "video_url": stream.url, "audio_url": stream.audio_url,
            "program_id": stream.program_id, "stream_type": stream.stream_type,
            "started_at": now.isoformat(), "output_file": str(output_path),
        }
        (self.metadata_dir / f"{filename}.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        self.metadata.save_metadata(info, self.metadata_dir)

        cmd = self._build_cmd(stream, temp_path)
        print(f"[Recorder] Starting: {stream.name}")
        self.telegram.notify_job_started("record", stream.name, stream.url)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    preexec_fn=os.setsid if hasattr(os, "setsid") else None)
        except Exception as e:
            print(f"[Recorder] ffmpeg failed for {stream.name}: {e}")
            return False

        with self._lock:
            self.active[key] = proc
            self.reconnects.setdefault(key, 0)
        threading.Thread(target=self._monitor, args=(key, stream, temp_path, output_path),
                         daemon=True).start()
        return True

    def _monitor(self, key: str, stream: StreamInfo, temp_path: Path, output_path: Path):
        status = ""
        while not self._stop.is_set():
            with self._lock:
                proc = self.active.get(key)
            if proc is None:
                status = "Stopped by user"
                break
            if proc.poll() is None:
                time.sleep(5)
                continue
            status = f"ffmpeg exited ({proc.returncode})"
            print(f"[Recorder] {status} for {stream.name}")
            break

        if temp_path.exists() and temp_path.stat().st_size > 1024:
            try:
                temp_path.rename(output_path)
                print(f"[Recorder] Saved: {output_path}")
            except Exception as e:
                print(f"[Recorder] Rename failed: {e}")

        with self._lock:
            self.active.pop(key, None)
            count = self.reconnects.get(key, 0)

        will_reconnect = False
        if not self._stop.is_set() and stream.stream_type == "live" and count < self.max_reconnects:
            print(f"[Recorder] Reconnecting {stream.name} in {self.reconnect_delay}s (#{count + 1})")
            time.sleep(self.reconnect_delay)
            fresh = next((s for s in self.finder.discover() if s.name == stream.name), None)
            if fresh and fresh.url:
                stream.url = fresh.url
                stream.audio_url = fresh.audio_url
                self.reconnects[key] = count + 1
                self.start_recording(stream)
                will_reconnect = True
            else:
                status = f"Stream {stream.name} no longer available"

        if not will_reconnect:
            out = str(output_path) if output_path.exists() else None
            self.telegram.notify_job_completed("record", stream.name, out, status or "Finished")

    def stop_recording(self, key: str) -> bool:
        with self._lock:
            proc = self.active.get(key)
        if proc is None:
            return False
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
        except Exception as e:
            print(f"[Recorder] Stop error: {e}")
        with self._lock:
            self.active.pop(key, None)
        return True

    def discover_and_record(self):
        print(f"[Recorder] Discovering at {datetime.now(timezone.utc).isoformat()}")
        streams = [s for s in self.finder.discover() if s.url]
        for s in streams:
            print(f"  - {s.name} ({s.stream_type})")
            with self._lock:
                already = s.name in self.active
            if not already:
                self.start_recording(s)

    def run(self):
        print(f"[Recorder] Daemon started — output: {self.output_dir}")
        self.discover_and_record()
        while not self._stop.is_set():
            self._stop.wait(self.check_interval)
            if not self._stop.is_set():
                self.discover_and_record()
        print("[Recorder] Shutting down")
        for k in list(self.active.keys()):
            self.stop_recording(k)

    def stop(self):
        self._stop.set()


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    rec = ArteRecorder(cfg)
    pid_file = Path("recorder.pid")
    pid_file.write_text(str(os.getpid()))
    try:
        rec.run()
    finally:
        pid_file.unlink(missing_ok=True)
