#!/usr/bin/env python3
"""Extract and store metadata for ARTE streams (via the player config API)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from stream_finder import (
    extract_arte_program_id,
    arte_language_from_url,
    ARTE_CONFIG_API,
)


class ArteMetadata:
    def __init__(self, page_url: str, user_agent: str):
        self.page_url = page_url
        self.headers = {"User-Agent": user_agent}
        self._config = None
        self._page_info = None

    def _fetch_config(self) -> dict:
        if self._config is not None:
            return self._config
        program_id = extract_arte_program_id(self.page_url)
        if not program_id:
            self._config = {}
            return self._config
        lang = arte_language_from_url(self.page_url)
        api_url = ARTE_CONFIG_API.format(lang=lang, program_id=program_id)
        try:
            resp = requests.get(api_url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            self._config = resp.json() or {}
        except Exception as e:
            print(f"[Metadata] ARTE config error: {e}")
            self._config = {}
        return self._config

    def extract_page_info(self) -> dict:
        if self._page_info is not None:
            return self._page_info
        cfg = self._fetch_config()
        attrs = cfg.get("data", {}).get("attributes", {})
        meta = attrs.get("metadata", {}) or {}
        rights = attrs.get("rights", {}) or {}
        info = {
            "title": meta.get("title", ""),
            "subtitle": meta.get("subtitle", ""),
            "description": meta.get("description", ""),
            "url": self.page_url,
            "live": bool(attrs.get("live")),
            "rights_begin": rights.get("begin"),
            "rights_end": rights.get("end"),
            "program_id": extract_arte_program_id(self.page_url),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        self._page_info = info
        return info

    def save_metadata(self, stream_info: dict, output_dir) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        name = stream_info.get("name", "unknown")
        safe = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name).strip().replace(" ", "_")
        filepath = output_dir / f"metadata_{safe}_{now.strftime('%Y%m%d_%H%M%S')}.json"
        data = {
            **stream_info,
            "recorded_at": now.isoformat(),
            "page_info": self.extract_page_info(),
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return filepath


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python metadata.py <arte_url>")
        sys.exit(1)
    print(json.dumps(ArteMetadata(sys.argv[1], "Mozilla/5.0").extract_page_info(),
                     indent=2, ensure_ascii=False))
