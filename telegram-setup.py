#!/usr/bin/env python3
"""Helper script to find Telegram chat ID and thread ID for the ARTE Recorder."""

import json
import sys
import time

import requests


def get_updates(bot_token: str, offset: int = 0):
    """Fetch recent updates from the Telegram bot."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"offset": offset, "limit": 20}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching updates: {e}")
        return None


def print_help():
    print("""
=== Telegram Setup Helper for ARTE Recorder ===

This script helps you find:
  - Your chat ID (for private messages or group ID)
  - Your thread ID (for topics in forum supergroups)

Steps:
1. Create a Telegram group/supergroup
2. Enable "Topics" if you want separate threads (optional)
3. Add your bot to the group
4. Send a message in the group/topic
5. Run this script with your bot token

Usage:
  python3 telegram-setup.py <YOUR_BOT_TOKEN>

Example:
  python3 telegram-setup.py 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
""")


def main():
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)

    bot_token = sys.argv[1]

    print("Fetching updates... send a message in your group/topic now if you haven't already.")
    print("(Waiting 3 seconds for any recent messages to arrive)\n")
    time.sleep(3)

    data = get_updates(bot_token)
    if not data or not data.get("ok"):
        print(f"Failed to get updates: {data}")
        sys.exit(1)

    updates = data.get("result", [])
    if not updates:
        print("No updates found. Make sure you:")
        print("  1. Started a conversation with the bot (for private chat)")
        print("  2. Added the bot to a group and sent a message (for groups)")
        sys.exit(1)

    print(f"Found {len(updates)} recent update(s):\n")

    found = set()
    for update in updates:
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue

        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        chat_title = chat.get("title") or chat.get("username") or "Private Chat"
        chat_type = chat.get("type", "unknown")
        thread_id = msg.get("message_thread_id")
        text = msg.get("text", "[no text]")

        key = (chat_id, thread_id)
        if key in found:
            continue
        found.add(key)

        print("─" * 50)
        print(f"Chat Title: {chat_title}")
        print(f"Chat Type:  {chat_type}")
        print(f"Chat ID:    {chat_id}")
        if thread_id:
            print(f"Thread ID:  {thread_id}  <-- use this for forum topics")
        else:
            print(f"Thread ID:  (not a forum topic)")
        print(f"Message:    {text[:60]}")
        print()

    print("─" * 50)
    print("\nAdd these values to your config.json:\n")
    print(json.dumps({
        "telegram": {
            "bot_token": bot_token,
            "chat_id": "<CHAT_ID_FROM_ABOVE>",
            "thread_id": "<THREAD_ID_FROM_ABOVE_OR_LEAVE_EMPTY>"
        }
    }, indent=2))
    print()


if __name__ == "__main__":
    main()
