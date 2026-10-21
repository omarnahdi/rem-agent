"""Deterministic timeout command parsing and Discord timeout verification."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence


_TIMEOUT_COMMAND_RE = re.compile(
    r"\b(?:mute|muting|muted|timeout|time\s*out|timed\s*out)\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"\b(\d+)\s*-?\s*"
    r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)\b",
    re.IGNORECASE,
)
_MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60


def parse_timeout_command(
    message: Any,
    author_id: int,
    bot_id: int,
    referenced_message: Any | None = None,
) -> dict[str, Any] | None:
    """Parse an explicit timeout request. Caller must already verify Master and mention/reply intent.

    Supported examples include:
      - @Rem timeout @Member for 15 minutes
      - @Rem give @Member a 2-hour timeout
      - @Rem mute @Member for 3 hours
      - @Rem timeout me for 30 seconds

    A concrete target mention is preferred. For pronouns, a reply to Rem can
    resolve a target only when exactly one member is named in the replied-to
    message. The parser never guesses an ambiguous target.
    """
    text = (getattr(message, "content", "") or "").casefold()
    if not _TIMEOUT_COMMAND_RE.search(text):
        return None

    # A real user mention is the strongest target signal. Do not let a casual
    # phrase such as "tell me when done" override the actual mentioned target.
    mentions = [
        member.id
        for member in getattr(message, "mentions", [])
        if member.id != bot_id and not getattr(member, "bot", False)
    ]
    if mentions:
        target_id = int(mentions[0])
    elif re.search(r"\bme\b", text):
        target_id = int(author_id)
    else:
        pronoun_target = re.search(
            r"\b(?:him|her|them|that guy|this guy|that user|this user)\b",
            text,
        )
        if pronoun_target is None or referenced_message is None:
            return None
        guild = getattr(message, "guild", None)
        if guild is None:
            return None
        referenced_text = (
            getattr(referenced_message, "content", "") or ""
        ).casefold()
        candidates = []
        for member in getattr(guild, "members", []):
            if (
                member.id in {bot_id, author_id}
                or getattr(member, "bot", False)
            ):
                continue
            display_name = (getattr(member, "display_name", "") or "").strip()
            if display_name and re.search(
                rf"(?<!\w){re.escape(display_name.casefold())}(?!\w)",
                referenced_text,
            ):
                candidates.append(member)
        if len(candidates) != 1:
            return None
        target_id = int(candidates[0].id)

    duration_match = _DURATION_RE.search(text)
    if duration_match is None:
        return None

    amount = int(duration_match.group(1))
    unit = duration_match.group(2).casefold()
    if amount <= 0:
        return None

    if unit.startswith(("second", "sec", "s")):
        seconds = amount
    elif unit.startswith(("minute", "min", "m")):
        seconds = amount * 60
    elif unit.startswith(("hour", "hr", "h")):
        seconds = amount * 60 * 60
    elif unit.startswith(("day", "d")):
        seconds = amount * 60 * 60 * 24
    elif unit.startswith(("week", "w")):
        seconds = amount * 60 * 60 * 24 * 7
    else:
        return None

    if seconds < 1 or seconds > _MAX_TIMEOUT_SECONDS:
        return None

    return {
        "user_id": target_id,
        "seconds": seconds,
        "amount": amount,
        "unit": unit,
    }


def get_member_timeout_until(member: Any) -> datetime | None:
    """Read the timeout deadline across discord.py versions.

    Recent discord.py releases expose the member attribute as
    ``timed_out_until`` while older releases expose
    ``communication_disabled_until``. The Discord API payload key remains
    ``communication_disabled_until``; relying only on the old Python attribute
    makes every verification appear to return ``None`` on newer releases.
    """
    for attribute in ("timed_out_until", "communication_disabled_until"):
        try:
            deadline = getattr(member, attribute, None)
        except Exception:
            continue
        if deadline is not None:
            return deadline
    return None


def timeout_deadline_matches(
    expected_until: datetime,
    actual_until: datetime | None,
    *,
    tolerance_seconds: float = 10.0,
) -> bool:
    """Check that the stored timeout deadline matches the requested deadline."""
    if actual_until is None:
        return False
    if expected_until.tzinfo is None:
        expected_until = expected_until.replace(tzinfo=timezone.utc)
    if actual_until.tzinfo is None:
        actual_until = actual_until.replace(tzinfo=timezone.utc)
    difference = abs((actual_until - expected_until).total_seconds())
    return difference <= max(0.0, tolerance_seconds)


async def apply_and_verify_timeout(
    guild: Any,
    member: Any,
    duration_seconds: int,
    reason: str,
    *,
    verification_attempts: int = 4,
    retry_delays: Sequence[float] = (0.0, 0.25, 0.75, 1.5),
) -> dict[str, Any]:
    """Apply a timeout until an exact UTC deadline and read it back from Discord.

    The request uses an absolute deadline so the value we verify is the same
    timestamp we sent. State reads are retried briefly to avoid a transient or
    stale member response being treated as a definitive verification failure.
    This function never treats a successful write alone as verified success.
    """
    try:
        seconds = int(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration_seconds must be an integer") from exc
    if seconds < 1 or seconds > _MAX_TIMEOUT_SECONDS:
        raise ValueError("Timeout must be between 1 second and 28 days")

    expected_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    await member.timeout(expected_until, reason=(reason or "Requested through Rem")[:500])

    attempts = max(1, int(verification_attempts))
    delays = tuple(max(0.0, float(delay)) for delay in retry_delays)
    actual_until: datetime | None = None
    verified_member: Any = None
    last_error: str | None = None
    attempts_used = 0

    for attempt in range(attempts):
        if attempt > 0:
            delay_index = min(attempt, len(delays) - 1) if delays else -1
            if delay_index >= 0 and delays[delay_index] > 0:
                await asyncio.sleep(delays[delay_index])

        attempts_used += 1
        try:
            verified_member = await guild.fetch_member(int(member.id))
            last_error = None
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        actual_until = get_member_timeout_until(verified_member)
        if timeout_deadline_matches(expected_until, actual_until):
            return {
                "verified": True,
                "expected_until": expected_until,
                "actual_until": actual_until,
                "verified_member": verified_member,
                "attempts_used": attempts_used,
                "verification_error": None,
            }

    return {
        "verified": False,
        "expected_until": expected_until,
        "actual_until": actual_until,
        "verified_member": verified_member,
        "attempts_used": attempts_used,
        "verification_error": last_error,
    }
