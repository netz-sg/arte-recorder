#!/usr/bin/env python3
"""Telegram notification helper for the ARTE Recorder."""

import os
from typing import Optional

import requests


# --- Visual building blocks for tidy, consistent messages -------------------
_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"


def _field(emoji: str, label: str, value: Optional[str], code: bool = False) -> Optional[str]:
    """Render one labelled line, or None if there is no value (so it's skipped)."""
    if not value:
        return None
    shown = f"<code>{value}</code>" if code else value
    return f"{emoji} <b>{label}:</b> {shown}"


def _join(*lines: Optional[str]) -> str:
    """Join the non-empty lines into a message body."""
    return "\n".join(l for l in lines if l)


_TYPE_NOUN = {"record": "Aufnahme", "download": "Download", "remux": "Konvertierung"}


class TelegramNotifier:
    API_BASE = "https://api.telegram.org/bot"

    def __init__(self, bot_token: Optional[str], chat_id: Optional[str], thread_id: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.enabled = bool(bot_token and chat_id)

    @classmethod
    def from_config(cls, config: dict):
        """Create notifier from config dict, with environment variable overrides."""
        telegram_cfg = config.get("telegram", {})
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or telegram_cfg.get("bot_token")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID") or telegram_cfg.get("chat_id")
        thread_id = os.environ.get("TELEGRAM_THREAD_ID") or telegram_cfg.get("thread_id")
        return cls(bot_token, chat_id, thread_id)

    def _api_url(self, method: str) -> str:
        return f"{self.API_BASE}{self.bot_token}/{method}"

    def send_message(self, text: str) -> bool:
        """Send a plain text message."""
        if not self.enabled:
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if self.thread_id:
            try:
                payload["message_thread_id"] = int(self.thread_id)
            except (ValueError, TypeError):
                print(f"[Telegram] Invalid thread_id: {self.thread_id}")
                return False

        try:
            resp = requests.post(self._api_url("sendMessage"), json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                print(f"[Telegram] API error: {data}")
                return False
            return True
        except Exception as e:
            print(f"[Telegram] Failed to send message: {e}")
            return False

    def get_updates(self, offset: Optional[int] = None, timeout: int = 30):
        """Long-poll for incoming bot updates (messages/commands).

        Returns a list of update dicts (possibly empty) on success, or None on a
        network/API error so the caller can back off."""
        if not self.enabled:
            return None
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(self._api_url("getUpdates"), params=params, timeout=timeout + 15)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                print(f"[Telegram] getUpdates error: {data}")
                return None
            return data.get("result", [])
        except Exception as e:
            print(f"[Telegram] getUpdates failed: {e}")
            return None

    def notify_job_started(self, job_type: str, name: str, url: str,
                           artist: Optional[str] = None, stage: Optional[str] = None) -> bool:
        """Notify that a job has started. When the playing artist/stage is known
        (live recordings), it is shown as the headline."""
        emoji = {"record": "🔴", "remux": "🔄", "upload": "⬆️"}.get(job_type, "⬇️")
        noun = _TYPE_NOUN.get(job_type, "Vorgang")
        head = f"{noun} gestartet"

        if artist:
            hero = f"🎤 <b>{artist}</b>"
            channel = _field("📺", "Stream", name)
        else:
            hero = f"📺 <b>{name}</b>"
            channel = None

        text = _join(
            f"{emoji} <b>{head}</b>",
            _DIVIDER,
            hero,
            _field("🎬", "Bühne", stage),
            channel,
            f'🔗 <a href="{url}">Stream öffnen</a>' if url else None,
        )
        return self.send_message(text)

    def notify_job_completed(self, job_type: str, name: str, output_file: Optional[str],
                             message: str, artist: Optional[str] = None,
                             stage: Optional[str] = None) -> bool:
        """Notify that a job has completed, failed or was stopped."""
        noun = _TYPE_NOUN.get(job_type, "Vorgang")
        msg = (message or "").lower()
        if any(k in msg for k in ("failed", "error", "fehlgeschlagen")):
            emoji, head = "❌", f"{noun} fehlgeschlagen"
        elif any(k in msg for k in ("stopped", "gestoppt")):
            emoji, head = "🛑", f"{noun} gestoppt"
        else:
            emoji, head = "✅", f"{noun} fertig"

        if artist:
            hero = f"🎤 <b>{artist}</b>"
            channel = _field("📺", "Stream", name)
        else:
            hero = f"📺 <b>{name}</b>"
            channel = None

        # Don't repeat the filename if the status line already mentions it.
        status = None if (output_file and output_file in (message or "")) else message

        text = _join(
            f"{emoji} <b>{head}</b>",
            _DIVIDER,
            hero,
            _field("🎬", "Bühne", stage),
            channel,
            _field("📂", "Datei", output_file, code=True),
            _field("ℹ️", "Status", status),
        )
        return self.send_message(text)


def get_thread_id_help() -> str:
    """Return instructions for finding Telegram thread IDs."""
    return """
How to find your Telegram thread ID (message_thread_id):

1. Create a Telegram group/supergroup and enable "Topics" (Forum)
   - Group Settings → Enable "Topics"

2. Add your bot to the group
   - Make sure the bot has permission to send messages

3. Create a topic/thread (or use an existing one)

4. Send ANY message in that specific topic

5. Open this URL in your browser:
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates

6. Look for the most recent update. You will see something like:
   {
     "message": {
       "chat": {"id": -1001234567890, "type": "supergroup"},
       "message_thread_id": 42,
       "text": "test"
     }
   }

7. The "message_thread_id" value (e.g. 42) is your thread ID.
   The "chat.id" (e.g. -1001234567890) is your chat ID.

Alternative: Use @getidsbot or @raw_data_bot in the topic to get the ID.
"""


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "help":
        print(get_thread_id_help())
        sys.exit(0)

    # Simple test if config is available
    import json
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        notifier = TelegramNotifier.from_config(cfg)
        if notifier.enabled:
            success = notifier.send_message("🧪 <b>Test message</b> from ARTE Recorder")
            print(f"Test message {'sent' if success else 'failed'}")
        else:
            print("Telegram not configured. Add bot_token and chat_id to config.json")
            print(get_thread_id_help())
    except FileNotFoundError:
        print("config.json not found")
