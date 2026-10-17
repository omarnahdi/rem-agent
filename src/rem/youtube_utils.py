"""Pure helpers for normalizing public Piped API search responses."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


def extract_piped_items(payload: Any) -> list[Any]:
    """Accept the legacy Piped object-with-items shape and documented list variants."""
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("results") or []
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("Unexpected Piped search response format")
    if not isinstance(items, list):
        raise ValueError("Piped search response did not contain a video list")
    return items


def format_duration(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        total_seconds = max(0, int(value))
    except (TypeError, ValueError):
        return str(value)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def format_published(value: Any) -> str:
    """Normalize timestamps while preserving relative strings if that's what Piped supplies."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return str(value)
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        return text


def normalize_piped_results(items: list[Any], limit: int = 8) -> list[dict[str, Any]]:
    """Convert Piped stream entries to a small, validated set of YouTube URLs."""
    limit = max(1, min(int(limit), 10))
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").casefold()
        if item_type and item_type != "stream":
            continue

        direct_url = str(item.get("url") or item.get("link") or "").strip()
        video_id = str(item.get("id") or item.get("videoId") or "").strip()
        url = ""
        if direct_url.startswith(("/watch", "/shorts/", "/embed/", "/live/")):
            url = "https://www.youtube.com" + direct_url
        elif direct_url.startswith(("http://", "https://")):
            parsed = urlparse(direct_url)
            host = (parsed.hostname or "").casefold()
            if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
                url = "https://www.youtube.com" + (parsed.path or "/")
                if parsed.query:
                    url += "?" + parsed.query
            elif host == "youtu.be":
                short_id = parsed.path.strip("/").split("/", 1)[0]
                if short_id:
                    url = f"https://www.youtube.com/watch?v={short_id}"
        elif video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"

        if not url.startswith("https://www.youtube.com/") or url in seen:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        seen.add(url)
        description = str(item.get("shortDescription") or item.get("description") or "").strip()[:1200]
        published = (
            item.get("uploadDate")
            or item.get("published")
            or item.get("publishedTime")
            or item.get("uploadedDate")
            or item.get("uploaded")
        )
        channel = str(
            item.get("uploaderName")
            or item.get("channelName")
            or item.get("uploader")
            or "Unknown channel"
        ).strip()
        duration = item.get("duration")
        results.append({
            "title": title,
            "channel": channel,
            "duration_seconds": duration,
            "duration": format_duration(duration),
            "views": item.get("views"),
            "published": format_published(published),
            "description": description,
            "url": url,
        })
        if len(results) >= limit:
            break
    return results
