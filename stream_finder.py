#!/usr/bin/env python3
"""Discover stream URLs from ARTE / ARTE Concert pages.

ARTE exposes a clean public player config API that returns the HLS master
playlist directly — no browser automation or scraping needed. This module
resolves a page/program URL to the highest-quality video variant plus ARTE's
separate audio rendition.
"""

import json
import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests


# ARTE player config API. Program IDs look like 132146-001-A; the site languages
# are de, fr, en, es, pl, it.
ARTE_CONFIG_API = "https://api.arte.tv/api/player/v2/config/{lang}/{program_id}"

_ARTE_PROGRAM_RE = re.compile(r'(\d{5,}-\d{3}-[A-Z])')
_ARTE_LANG_RE = re.compile(r'arte\.tv/([a-z]{2})/')


def is_arte_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return "arte.tv" in u or "api.arte.tv" in u


def arte_language_from_url(url: str, default: str = "de") -> str:
    m = _ARTE_LANG_RE.search(url or "")
    return m.group(1) if m else default


def extract_arte_program_id(url: str) -> Optional[str]:
    """Extract an ARTE program id (e.g. 132146-001-A) from a page or API URL."""
    if not url:
        return None
    m = _ARTE_PROGRAM_RE.search(url)
    return m.group(1) if m else None


@dataclass
class StreamInfo:
    name: str
    url: str                       # best video variant (HLS media playlist)
    program_id: Optional[str] = None
    stream_type: str = "unknown"   # live | vod
    quality: str = "best"
    # ARTE delivers audio as a separate EXT-X-MEDIA rendition, so ffmpeg needs
    # video + audio as two synchronized inputs to produce a file with sound.
    audio_url: Optional[str] = None


class ArteStreamFinder:
    """Resolve ARTE page/program URLs to recordable HLS streams."""

    def __init__(self, page_url: str, user_agent: str):
        self.page_url = page_url
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    # -- public API ---------------------------------------------------------

    def config_api_url(self) -> Optional[str]:
        program_id = extract_arte_program_id(self.page_url)
        if not program_id:
            return None
        lang = arte_language_from_url(self.page_url)
        return ARTE_CONFIG_API.format(lang=lang, program_id=program_id)

    def fetch_config(self) -> dict:
        api_url = self.config_api_url()
        if not api_url:
            print(f"[ArteStreamFinder] No ARTE program id in {self.page_url}")
            return {}
        try:
            resp = self.session.get(api_url, timeout=30)
            resp.raise_for_status()
            return resp.json() or {}
        except Exception as e:
            print(f"[ArteStreamFinder] Config API error: {e}")
            return {}

    def discover(self) -> List[StreamInfo]:
        """Return every recordable stream (one per available version)."""
        data = self.fetch_config()
        attrs = data.get("data", {}).get("attributes", {})
        meta = attrs.get("metadata", {}) or {}
        title = meta.get("title") or extract_arte_program_id(self.page_url) or "ARTE"
        program_id = extract_arte_program_id(self.page_url)
        stream_type = "live" if attrs.get("live") else "vod"

        raw_streams = attrs.get("streams", []) or []
        streams: List[StreamInfo] = []
        for entry in raw_streams:
            master = entry.get("url")
            if not master:
                continue
            versions = entry.get("versions") or []
            label = versions[0].get("label") if versions else None
            name = f"{title} ({label})" if (len(raw_streams) > 1 and label) else title

            best_video, audio_url = self.resolve_master_va(master)
            streams.append(StreamInfo(
                name=name,
                url=best_video or master,
                program_id=program_id,
                stream_type=stream_type,
                quality="best",
                audio_url=audio_url,
            ))

        if not streams:
            print(f"[ArteStreamFinder] No playable streams for {self.page_url}")
        return streams

    # Backwards-compatible alias used by the CLI/recorder.
    def discover_all(self) -> List[StreamInfo]:
        return self.discover()

    def resolve_master_va(self, master_url: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse an HLS master playlist -> (best_video_url, audio_url).

        Picks the highest-quality video variant (by bandwidth, then resolution)
        and, when audio is a separate EXT-X-MEDIA rendition, the matching audio
        playlist URL. For a media playlist (no variants) audio_url is None and
        best_video_url is the input unchanged.
        """
        try:
            resp = self.session.get(master_url, timeout=30)
            resp.raise_for_status()
            content = resp.text
        except Exception as e:
            print(f"[ArteStreamFinder] Failed to fetch master playlist: {e}")
            return master_url, None

        if "#EXT-X-STREAM-INF" not in content:
            return master_url, None  # already a media playlist

        base_url = master_url.rsplit("/", 1)[0] + "/"
        audio_uris = {}        # group_id -> uri
        variants = []          # (bandwidth, height, url, audio_group)
        cur_bw = cur_h = 0
        cur_audio_group = None

        for line in content.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in line:
                g = re.search(r'GROUP-ID="([^"]+)"', line)
                u = re.search(r'URI="([^"]+)"', line)
                if g and u:
                    audio_uris[g.group(1)] = u.group(1)
            elif line.startswith("#EXT-X-STREAM-INF"):
                bw = re.search(r'BANDWIDTH=(\d+)', line)
                res = re.search(r'RESOLUTION=\d+x(\d+)', line)
                ag = re.search(r'AUDIO="([^"]+)"', line)
                cur_bw = int(bw.group(1)) if bw else 0
                cur_h = int(res.group(1)) if res else 0
                cur_audio_group = ag.group(1) if ag else None
            elif line and not line.startswith("#"):
                vurl = line if line.startswith("http") else urljoin(base_url, line)
                variants.append((cur_bw, cur_h, vurl, cur_audio_group))
                cur_bw = cur_h = 0
                cur_audio_group = None

        if not variants:
            return master_url, None

        variants.sort(key=lambda v: (v[0], v[1]), reverse=True)
        best = variants[0]
        best_video = best[2]
        audio_url = None
        group = best[3]
        if group and group in audio_uris:
            au = audio_uris[group]
            audio_url = au if au.startswith("http") else urljoin(base_url, au)
        return best_video, audio_url

    def get_best_variant(self, master_url: str) -> Optional[str]:
        """Return only the best video variant URL (audio resolved separately)."""
        best_video, _ = self.resolve_master_va(master_url)
        return best_video


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python stream_finder.py <arte_url>")
        sys.exit(1)
    finder = ArteStreamFinder(sys.argv[1], "Mozilla/5.0")
    print(json.dumps([asdict(s) for s in finder.discover()], indent=2, ensure_ascii=False))
