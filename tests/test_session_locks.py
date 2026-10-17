import asyncio
import unittest

from rem.session_locks import SessionLockPool


class SessionLockPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_session_runs_are_serialized(self):
        pool = SessionLockPool()
        active = 0
        max_active = 0

        async def worker():
            nonlocal active, max_active
            async with pool.hold("same-session"):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.005)
                active -= 1

        await asyncio.gather(*(worker() for _ in range(8)))
        self.assertEqual(max_active, 1)
        self.assertEqual(pool.active_session_count, 0)

    async def test_different_sessions_can_run_in_parallel(self):
        pool = SessionLockPool()
        active = 0
        max_active = 0
        both_entered = asyncio.Event()

        async def worker(session_id: str):
            nonlocal active, max_active
            async with pool.hold(session_id):
                active += 1
                max_active = max(max_active, active)
                if active >= 2:
                    both_entered.set()
                await asyncio.wait_for(both_entered.wait(), timeout=1)
                active -= 1

        await asyncio.gather(worker("session-a"), worker("session-b"))
        self.assertEqual(max_active, 2)
        self.assertEqual(pool.active_session_count, 0)


if __name__ == "__main__":
    unittest.main()
