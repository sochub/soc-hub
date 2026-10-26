"""Slack Socket Mode runner: one outbound WebSocket per tenant that has an app-level token.

    python -m app.slack_socket

Slack delivers slash commands, interactivity and events over the socket instead of calling our HTTP
request URLs, so sochub needs no public endpoint for Slack. Each envelope is acknowledged (with the
response payload, for commands and modal submissions) through the shared dispatchers in slack_bot,
so behaviour is identical to the HTTP endpoints. Trust comes from the connection itself: Slack only
issues a socket URL for a valid app-level token, and each payload's team must match the integration.

The set of connections is reconciled with the database every RECONCILE_SECONDS, so adding, rotating or
removing a token in the UI takes effect without a restart. Liveness is published to Redis for the UI.
"""
import asyncio
import json
import logging
import signal
import time
from typing import Dict, Optional, Tuple

import websockets
from cryptography.fernet import InvalidToken
from sqlalchemy import select

import app.db.base  # noqa: F401 — register models
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.slack_integration import SlackIntegration
from app.services import slack_bot
from app.services.slack_service import SlackError, slack_call
from app.utils.crypto import decrypt

logger = logging.getLogger("app.slack_socket")
RECONCILE_SECONDS = 30
HEARTBEAT_TTL = 90
_MAX_BACKOFF = 60


def heartbeat_key(tenant_id: int) -> str:
    return f"slack:socket:{tenant_id}"


def _team_of(kind: str, payload: dict) -> Optional[str]:
    if kind == "slash_commands":
        return payload.get("team_id")
    if kind == "events_api":
        return payload.get("team_id")
    return (payload.get("team") or {}).get("id")


async def handle_envelope(integration_id: int, msg: dict) -> Optional[dict]:
    """Process one Socket Mode envelope; returns the ack payload (None = plain ack)."""
    kind, payload = msg.get("type"), msg.get("payload") or {}
    async with AsyncSessionLocal() as db:
        integ = await db.get(SlackIntegration, integration_id)
        if integ is None:
            return None
        team = _team_of(kind, payload)
        if integ.team_id and team != integ.team_id:
            logger.warning("dropping %s envelope for team %s on tenant %s socket", kind, team, integ.tenant_id)
            return None
        if kind == "slash_commands":
            return await slack_bot.dispatch_command(db, integ, payload)
        if kind == "interactive":
            return await slack_bot.dispatch_interactive(db, integ, payload)
        if kind == "events_api":
            await asyncio.to_thread(slack_bot.dispatch_event, integ, payload)  # Celery .delay blocks on the broker
    return None


class Connection:
    """Keeps one tenant's socket open, reconnecting with backoff; Slack rotates sockets every few hours."""

    def __init__(self, integration_id: int, tenant_id: int, app_token: str, redis):
        self.integration_id, self.tenant_id, self.app_token, self.redis = integration_id, tenant_id, app_token, redis
        self._acks: set = set()  # strong refs: un-referenced tasks can be garbage-collected mid-flight
        self.task = asyncio.create_task(self.run())

    async def _beat(self) -> None:
        try:
            await self.redis.set(heartbeat_key(self.tenant_id), str(int(time.time())), ex=HEARTBEAT_TTL)
        except Exception:  # the UI badge is cosmetic; never drop the socket over Redis
            logger.debug("heartbeat failed", exc_info=True)

    async def _ack(self, ws, msg: dict) -> None:
        try:
            out = await handle_envelope(self.integration_id, msg)
        except Exception:
            logger.exception("Socket Mode envelope failed (tenant %s)", self.tenant_id)
            out = None
        await ws.send(json.dumps({"envelope_id": msg["envelope_id"], **({"payload": out} if out else {})}))

    async def run(self) -> None:
        backoff = 1
        while True:
            try:
                url = (await slack_call(self.app_token, "apps.connections.open"))["url"]
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2 ** 20) as ws:
                    logger.info("Socket Mode connected for tenant %s", self.tenant_id)
                    backoff = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "hello":
                            await self._beat()
                        elif msg.get("type") == "disconnect":
                            break  # Slack is rotating this socket: reconnect
                        elif msg.get("envelope_id"):
                            await self._beat()
                            # Ack concurrently: one slow modal must not hold up the rest of the socket.
                            t = asyncio.create_task(self._ack(ws, msg))
                            self._acks.add(t)
                            t.add_done_callback(self._acks.discard)
            except asyncio.CancelledError:
                raise
            except SlackError as e:
                logger.warning("Socket Mode open failed for tenant %s: %s", self.tenant_id, e.code or e)
                if e.code in ("invalid_auth", "not_authed", "token_revoked", "account_inactive"):
                    backoff = _MAX_BACKOFF  # bad token: retry slowly until an admin fixes it
            except Exception:
                logger.warning("Socket Mode connection dropped for tenant %s", self.tenant_id, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)

    def stop(self) -> None:
        self.task.cancel()


async def _desired() -> Dict[int, Tuple[int, str]]:
    """integration id -> (tenant id, app token) for every integration with a usable app-level token."""
    out = {}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(SlackIntegration).where(SlackIntegration.app_token_enc.isnot(None)))).scalars().all()
        for i in rows:
            try:
                out[i.id] = (i.tenant_id, decrypt(i.app_token_enc))
            except InvalidToken:
                logger.warning("undecryptable Slack app token for tenant %s (SECRET_KEY changed?)", i.tenant_id)
    return out


async def main() -> None:
    import redis.asyncio as aioredis

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redis = aioredis.from_url(settings.REDIS_URL)
    conns: Dict[int, Connection] = {}
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            try:
                want = await _desired()
            except Exception:
                logger.exception("could not load Slack integrations")
                want = {iid: (c.tenant_id, c.app_token) for iid, c in conns.items()}
            for iid, conn in list(conns.items()):
                if want.get(iid) != (conn.tenant_id, conn.app_token):  # removed or token rotated
                    conn.stop()
                    del conns[iid]
            for iid, (tid, token) in want.items():
                if iid not in conns:
                    conns[iid] = Connection(iid, tid, token, redis)
            try:
                await asyncio.wait_for(stop.wait(), RECONCILE_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        for conn in conns.values():
            conn.stop()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
