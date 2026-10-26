"""War-room schema: listen flag default, suggestion uniqueness, per-message dedupe."""
import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.war_room_suggestion import WarRoomMessageSeen, WarRoomSuggestion
from tests.guest_fixtures import make_case, make_tenant, run


def test_case_listen_defaults_false():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = Case(title="listen-default", tenant_id=t.id)
            db.add(c)
            await db.flush()
            ids.cases.append(int(c.id))
            assert c.slack_listen is False
            assert c.slack_channel_name is None and c.slack_channel_created_at is None
            await db.commit()
    run(body)


def test_suggestion_unique_value_key():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            kw = dict(tenant_id=t.id, case_id=c.id, kind="artifact", value_key="ip:1.2.3.4", payload={})
            first = WarRoomSuggestion(**kw)
            db.add(first)
            await db.commit()
            assert first.status == "pending"
            db.add(WarRoomSuggestion(**kw))
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()
            # a different kind with the same value_key is allowed
            db.add(WarRoomSuggestion(**{**kw, "kind": "milestone"}))
            await db.commit()
    run(body)


def test_message_seen_pk():
    async def body(ids):
        ch, ts = f"C{ids.h}", "1700000000.000100"
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            try:
                db.add(WarRoomMessageSeen(slack_channel=ch, ts=ts, tenant_id=t.id))
                await db.commit()
                db.add(WarRoomMessageSeen(slack_channel=ch, ts=ts, tenant_id=t.id))
                with pytest.raises(IntegrityError):
                    await db.commit()
                await db.rollback()
            finally:
                await db.execute(delete(WarRoomMessageSeen).where(
                    WarRoomMessageSeen.slack_channel == ch, WarRoomMessageSeen.ts == ts))
                await db.commit()
    run(body)
