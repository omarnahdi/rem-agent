"""Deterministic parsing of explicit Master timeout commands."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def parse_timeout_command(message: Any, author_id: int, bot_id: int, referenced_message: Any | None = None) -> dict[str, Any] | None:
    """Parse an explicit timeout request. Caller must already verify Master and mention/reply intent."""
    text = (getattr(message, "content", "") or "").casefold()
    if not re.search(r"\b(?:mute|muting|muted|timeout|time\s*out|timed\s*out)\b", text):
        return None

    if re.search(r"\bme\b", text):
        target_id = int(author_id)
    else:
        mentions = [m.id for m in getattr(message, "mentions", []) if m.id != bot_id and not getattr(m, "bot", False)]
        if mentions:
            target_id = int(mentions[0])
        else:
            if not re.search(r"\b(?:him|her|them|that guy|this guy|that user|this user)\b", text) or referenced_message is None:
                return None
            guild = getattr(message, "guild", None)
            if guild is None:
                return None
            referenced_text = (getattr(referenced_message, "content", "") or "").casefold()
            candidates = []
            for member in getattr(guild, "members", []):
                if member.id in {bot_id, author_id} or getattr(member, "bot", False):
                    continue
                display_name = (getattr(member, "display_name", "") or "").strip()
                if display_name and re.search(rf"(?<!\w){re.escape(display_name.casefold())}(?!\w)", referenced_text):
                    candidates.append(member)
            if len(candidates) != 1:
                return None
            target_id = int(candidates[0].id)

    duration = re.search(r"\b(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)\b", text)
    if not duration:
        return None
    amount = int(duration.group(1))
    unit = duration.group(2).casefold()
    if amount <= 0:
        return None
    if unit.startswith(("second", "sec", "s")):
        seconds = amount
    elif unit.startswith(("minute", "min", "m")):
        seconds = amount * 60
    elif unit.startswith(("hour", "hr", "h")):
        seconds = amount * 3600
    elif unit.startswith(("day", "d")):
        seconds = amount * 86400
    elif unit.startswith(("week", "w")):
        seconds = amount * 604800
    else:
        return None
    if seconds > 28 * 24 * 3600:
        return None
    return {"user_id": target_id, "seconds": seconds, "amount": amount, "unit": unit}


def timeout_deadline_matches(
    expected_until: datetime,
    actual_until: datetime | None,
    *,
    tolerance_seconds: float = 10.0,
) -> bool:
    """Verify Discord stored the requested timeout deadline, not merely any timeout.

    Discord timestamps are UTC-aware in normal discord.py responses, but naive
    datetimes are treated as UTC to keep the check deterministic in tests.
    """
    if actual_until is None:
        return False
    if expected_until.tzinfo is None:
        expected_until = expected_until.replace(tzinfo=timezone.utc)
    if actual_until.tzinfo is None:
        actual_until = actual_until.replace(tzinfo=timezone.utc)
    difference = abs((actual_until - expected_until).total_seconds())
    return difference <= max(0.0, tolerance_seconds)
