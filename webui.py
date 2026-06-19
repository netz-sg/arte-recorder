#!/usr/bin/env python3
"""Lean web UI for the ARTE Recorder.

Paste an ARTE / ARTE Concert URL, discover the stream, then record (live) or
download (VOD) it in best quality with sound. Recordings appear in the archive.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory, abort

from stream_finder import ArteStreamFinder, is_arte_url
from metadata import ArteMetadata
from notifications import TelegramNotifier

CONFIG_PATH = os.environ.get("ARTE_CONFIG", "config.json")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


CONFIG = load_config()
UA = CONFIG["user_agent"]
OUTPUT_DIR = Path(CONFIG["output_dir"]).resolve()
TEMP_DIR = Path(CONFIG["temp_dir"]).resolve()
METADATA_DIR = Path(CONFIG["metadata_dir"]).resolve()
for _d in (OUTPUT_DIR, TEMP_DIR, METADATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

RECORD_EXT = ".mkv" if str(CONFIG.get("record_format", "mp4")).lower().lstrip(".") == "mkv" else ".mp4"
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".ts", ".m4a", ".mov"}

telegram = TelegramNotifier.from_config(CONFIG)
app = Flask(__name__)


# --- jobs -------------------------------------------------------------------

@dataclass
class Job:
    id: str
    type: str               # record | download
    name: str
    video_url: str
    audio_url: Optional[str] = None
    status: str = "starting"   # starting | running | completed | failed | stopped
    message: str = ""
    progress: dict = field(default_factory=dict)
    output_file: Optional[str] = None
    started_at: str = ""
    stop_requested: bool = False
    process: Optional[object] = None

    def public(self) -> dict:
        d = asdict(self)
        d.pop("process", None)
        return d


jobs: Dict[str, Job] = {}
jobs_lock = threading.Lock()


def _sanitize(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name)
    return safe.strip().replace(" ", "_") or "arte"


def _ffmpeg_cmd(video_url: str, audio_url: Optional[str], out_path: Path,
                duration: Optional[str] = None) -> List[str]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
           *CONFIG.get("ffmpeg_extra_args", []), "-i", video_url]
    if audio_url:
        cmd += ["-i", audio_url]
    if duration:
        cmd += ["-t", str(duration)]
    if audio_url:
        cmd += ["-c", "copy", "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-c", "copy", "-map", "0"]
    cmd += ["-flush_packets", "1", "-f", "matroska", str(out_path)]
    return cmd


_PROGRESS_PATTERNS = {
    "time": r"time=\s*([\d:.]+)",
    "bitrate": r"bitrate=\s*([\d.]+\s*kbits/s)",
    "speed": r"speed=\s*([\d.]+x)",
    "size": r"size=\s*(\d+\s*[kKmMgG]?i?B)",
}


def _parse_progress(line: str) -> dict:
    out = {}
    for key, pat in _PROGRESS_PATTERNS.items():
        m = re.search(pat, line)
        if m:
            out[key] = m.group(1).strip()
    return out


def _finalize(parts: List[Path], out_path: Path, job: Job) -> Optional[Path]:
    """Concat recorded parts (if >1) and remux to the configured container."""
    parts = [p for p in parts if p.exists() and p.stat().st_size > 1024]
    if not parts:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if len(parts) == 1 and out_path.suffix == ".mkv":
        parts[0].rename(out_path)
        return out_path

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
    if len(parts) == 1:
        cmd += ["-i", str(parts[0])]
    else:
        list_path = TEMP_DIR / f"concat_{job.id}.txt"
        list_path.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        cmd += ["-f", "concat", "-safe", "0", "-i", str(list_path)]
    cmd += ["-c", "copy"]
    if out_path.suffix == ".mp4":
        cmd += ["-movflags", "+faststart"]
    cmd += [str(out_path)]

    if subprocess.run(cmd).returncode == 0 and out_path.exists():
        for p in parts:
            p.unlink(missing_ok=True)
        return out_path
    # Fallback: keep the first part so nothing is lost.
    fallback = out_path.with_suffix(".mkv")
    if out_path.suffix != ".mkv":
        try:
            parts[0].rename(fallback)
            return fallback
        except Exception:
            return parts[0]
    return parts[0]


def _run_download(job: Job):
    job.status = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()
    telegram.notify_job_started("download", job.name, job.video_url)
    base = f"{_sanitize(job.name)}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    temp_path = TEMP_DIR / f"temp_{base}.mkv"
    out_path = OUTPUT_DIR / f"{base}{RECORD_EXT}"
    cmd = _ffmpeg_cmd(job.video_url, job.audio_url, temp_path)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        job.process = proc
        for line in proc.stdout:
            line = line.strip()
            p = _parse_progress(line)
            if p:
                job.progress.update(p)
                job.message = f"Download — {p.get('time', '?')} @ {p.get('speed', '?')}"
        proc.wait()
        produced = _finalize([temp_path], out_path, job)
        if proc.returncode == 0 and produced:
            job.output_file = produced.name
            job.status = "completed"
            job.message = f"Fertig: {produced.name}"
            telegram.notify_job_completed("download", job.name, produced.name, job.message)
        elif produced:
            job.output_file = produced.name
            job.status = "completed"
            job.message = f"Teilweise gespeichert: {produced.name}"
        else:
            job.status = "failed"
            job.message = "Download fehlgeschlagen"
            telegram.notify_job_completed("download", job.name, None, job.message)
    except Exception as e:
        job.status = "failed"
        job.message = f"Fehler: {e}"


def _run_record(job: Job):
    """Record a live stream into part files, reconnecting until stopped."""
    job.status = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()
    telegram.notify_job_started("record", job.name, job.video_url)
    base = f"{_sanitize(job.name)}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    out_path = OUTPUT_DIR / f"{base}{RECORD_EXT}"
    retry_delay = max(1, int(CONFIG.get("reconnect_delay_seconds", 5)))
    max_outage = int(CONFIG.get("record_max_outage_seconds", 600))
    parts: List[Path] = []
    attempt = 0
    last_good = time.time()

    while not job.stop_requested:
        attempt += 1
        part_path = TEMP_DIR / f"temp_{base}_p{attempt:03d}.mkv"
        parts.append(part_path)
        cmd = _ffmpeg_cmd(job.video_url, job.audio_url, part_path)
        job.message = ("Aufnahme läuft" if attempt == 1
                       else f"Reconnect #{attempt - 1} — Aufnahme läuft weiter")
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            job.process = proc
            for line in proc.stdout:
                line = line.strip()
                p = _parse_progress(line)
                if p:
                    job.progress.update(p)
                    job.message = f"Aufnahme — {p.get('time', '?')} @ {p.get('size', '?')}"
                    last_good = time.time()
            proc.wait()
        except Exception as e:
            job.message = f"ffmpeg-Fehler: {e}"

        if job.stop_requested:
            break
        # Refresh stream URL (live tokens rotate) before reconnecting.
        if part_path.exists() and part_path.stat().st_size > 1024:
            last_good = time.time()
        if time.time() - last_good > max_outage:
            job.message = "Stream zu lange offline — Aufnahme beendet"
            break
        try:
            finder = ArteStreamFinder(_job_source_url.get(job.id, job.video_url), UA)
            fresh = next((s for s in finder.discover() if s.url), None)
            if fresh:
                job.video_url, job.audio_url = fresh.url, fresh.audio_url
        except Exception:
            pass
        time.sleep(retry_delay)

    produced = _finalize(parts, out_path, job)
    if produced:
        job.output_file = produced.name
        job.status = "stopped" if job.stop_requested else "completed"
        job.message = f"Gespeichert: {produced.name}"
        telegram.notify_job_completed("record", job.name, produced.name, job.message)
    else:
        job.status = "failed"
        job.message = "Keine Daten aufgenommen (Stream evtl. noch nicht live)"
        telegram.notify_job_completed("record", job.name, None, job.message)


# Remember the page/source URL per job so reconnect can re-resolve fresh tokens.
_job_source_url: Dict[str, str] = {}


# --- resolving helpers ------------------------------------------------------

def _resolve_target(url: str):
    """Return (info, streams) for an ARTE page URL, or a direct m3u8 master.

    streams: list of dicts {name, url, audio_url, stream_type, quality}.
    """
    url = (url or "").strip()
    if ".m3u8" in url:
        finder = ArteStreamFinder(url, UA)
        video, audio = finder.resolve_master_va(url)
        return ({}, [{"name": "ARTE Stream", "url": video, "audio_url": audio,
                      "stream_type": "live", "quality": "best", "master_url": url}])
    finder = ArteStreamFinder(url, UA)
    streams = [asdict(s) for s in finder.discover() if s.url]
    info = ArteMetadata(url, UA).extract_page_info()
    return (info, streams)


# --- routes -----------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", default_url=CONFIG.get("page_url", ""))


@app.route("/api/discover", methods=["POST"])
def api_discover():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Keine URL angegeben"}), 400
    if not (is_arte_url(url) or ".m3u8" in url):
        return jsonify({"error": "Bitte eine arte.tv-URL (oder .m3u8) angeben"}), 400
    try:
        info, streams = _resolve_target(url)
        if not streams:
            return jsonify({"error": "Kein Stream gefunden", "info": info, "streams": []}), 404
        return jsonify({"info": info, "streams": streams})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _start_job(job_type: str, data: dict):
    name = (data.get("name") or "ARTE").strip()
    video_url = (data.get("video_url") or data.get("url") or "").strip()
    audio_url = (data.get("audio_url") or "").strip() or None
    source_url = (data.get("source_url") or data.get("url") or video_url).strip()

    # If we only got a page URL, resolve it now.
    if ".m3u8" not in video_url:
        _, streams = _resolve_target(video_url or source_url)
        if not streams:
            return None, "Kein Stream gefunden"
        s = streams[0]
        video_url, audio_url = s["url"], s.get("audio_url")
        name = name if name != "ARTE" else s.get("name", name)

    job_id = uuid.uuid4().hex[:8]
    job = Job(id=job_id, type=job_type, name=name, video_url=video_url, audio_url=audio_url)
    _job_source_url[job_id] = source_url or video_url
    with jobs_lock:
        jobs[job_id] = job
    runner = _run_record if job_type == "record" else _run_download
    threading.Thread(target=runner, args=(job,), daemon=True).start()
    return job, None


@app.route("/api/record", methods=["POST"])
def api_record():
    job, err = _start_job("record", request.get_json(force=True, silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    return jsonify(job.public())


@app.route("/api/download", methods=["POST"])
def api_download():
    job, err = _start_job("download", request.get_json(force=True, silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    return jsonify(job.public())


@app.route("/api/jobs")
def api_jobs():
    with jobs_lock:
        return jsonify([j.public() for j in sorted(jobs.values(),
                        key=lambda x: x.started_at or "", reverse=True)])


@app.route("/api/jobs/<job_id>/stop", methods=["POST"])
def api_stop(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job nicht gefunden"}), 404
    job.stop_requested = True
    proc = job.process
    if proc and proc.poll() is None:
        try:
            # Graceful quit so the file is finalized cleanly.
            proc.stdin.write("q")
            proc.stdin.flush()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
    return jsonify({"ok": True})


@app.route("/api/archive")
def api_archive():
    items = []
    for p in OUTPUT_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
            st = p.stat()
            items.append({
                "filename": p.name,
                "size": st.st_size,
                "size_human": _human_size(st.st_size),
                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            })
    items.sort(key=lambda x: x["modified"], reverse=True)
    return jsonify(items)


@app.route("/download/<path:filename>")
def download_file(filename: str):
    safe = Path(filename).name
    if not (OUTPUT_DIR / safe).exists():
        abort(404)
    return send_from_directory(OUTPUT_DIR, safe, as_attachment=True)


@app.route("/api/archive/<path:filename>", methods=["DELETE"])
def delete_file(filename: str):
    safe = Path(filename).name
    target = OUTPUT_DIR / safe
    if not target.exists():
        return jsonify({"error": "nicht gefunden"}), 404
    target.unlink()
    return jsonify({"ok": True})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        url = (data.get("page_url") or "").strip()
        if url:
            CONFIG["page_url"] = url
            save_config(CONFIG)
    return jsonify({"page_url": CONFIG.get("page_url", ""),
                    "record_format": CONFIG.get("record_format", "mp4"),
                    "telegram_enabled": telegram.enabled})


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARTE Recorder Web UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    print(f"Starting ARTE Recorder Web UI on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
