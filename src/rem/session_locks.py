"""Per-session asyncio locks to keep persisted conversation history ordered."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class SessionLockPool:
    """Serialize runs with the same session ID without retaining idle locks forever."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, session_id: str) -> AsyncIterator[None]:
        # Registry operations have no await, so reference counting is atomic on
        # this event loop. Count both the current holder and queued waiters.
        entry = self._entries.get(session_id)
        if entry is None:
            entry = (asyncio.Lock(), 0)
        lock, users = entry
        self._entries[session_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            current = self._entries.get(session_id)
            if current is not None:
                current_lock, current_users = current
                if current_users <= 1:
                    self._entries.pop(session_id, None)
                else:
                    self._entries[session_id] = (current_lock, current_users - 1)

    @property
    def active_session_count(self) -> int:
        """Number of sessions currently using or waiting for a lock."""
        return len(self._entries)
