"""Send picks to a Discord channel via webhook (set DISCORD_WEBHOOK_URL)."""

from __future__ import annotations

import json
import os
import urllib.request


def send_discord(lines: list[str], webhook: str | None = None) -> None:
    webhook = webhook or os.environ["DISCORD_WEBHOOK_URL"]
    # Discord caps messages at 2000 characters.
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 1900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    for chunk in chunks:
        body = json.dumps({"content": f"```\n{chunk}```"}).encode()
        req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json", "User-Agent": "larrybot"})
        urllib.request.urlopen(req, timeout=15).close()
