"""Sync entry for async task bodies. Imports nothing from app.worker or other task modules, so any task
module can use it without an import cycle (the slack-socket process imports task modules cold)."""
import asyncio


def _run_async(coro):
    """Run a coroutine in a fresh loop, then dispose pooled connections bound to it
    (same reason as app/tasks/triage.py)."""
    from app.db.session import engine

    async def wrapper():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(wrapper())
