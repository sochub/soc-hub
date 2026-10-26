#!/usr/bin/env python3
"""Re-encrypt every stored secret with the current encryption key.

Usage:

    python -m app.scripts.reencrypt_secrets

Run it after deploying key separation (values written under the legacy SHA-256(SECRET_KEY) key move to the
HKDF-derived key) and after every ENCRYPTION_KEY change (keep the old value in ENCRYPTION_KEYS_PREVIOUS
until the script has finished on every environment).

Covers every `*_enc` column of every model. Idempotent: values already under the current key are left
untouched. Each table is one transaction, read in batches; an update only applies if the stored value is
still the one that was read (a concurrent save wins). Values no configured key can open are reported and
left as they are, never overwritten. Only counts are logged, never values.
"""
import asyncio
import logging
import sys
from typing import Iterable, List, Optional, Tuple

from cryptography.fernet import InvalidToken
from sqlalchemy import Table, select, update

import app.db.base  # noqa: F401 — register all models before any query
from app.db.base_class import Base
from app.db.session import AsyncSessionLocal, engine
from app.utils import crypto

logger = logging.getLogger("reencrypt_secrets")


def encrypted_columns() -> List[Tuple[Table, List[str]]]:
    out = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        cols = [c.name for c in table.columns if c.name.endswith("_enc")]
        if cols:
            out.append((table, cols))
    return out


async def reencrypt_table(table: Table, columns: Iterable[str], ids: Optional[List[int]] = None,
                          batch_size: int = 500) -> dict:
    """Re-encrypt `columns` of `table` in one transaction. `ids` limits it to those primary keys."""
    (pk,) = table.primary_key.columns
    columns = list(columns)
    counts = {"updated": 0, "current": 0, "undecryptable": 0, "null": 0}
    async with AsyncSessionLocal() as db:
        last = None
        while True:
            q = select(pk, *[table.c[c] for c in columns]).order_by(pk).limit(batch_size)
            if last is not None:
                q = q.where(pk > last)
            if ids is not None:
                q = q.where(pk.in_(ids))
            rows = (await db.execute(q)).all()
            if not rows:
                break
            for row in rows:
                for col, value in zip(columns, row[1:]):
                    if value is None:
                        counts["null"] += 1
                    elif crypto.is_current(value):
                        counts["current"] += 1
                    else:
                        try:
                            new = crypto.rotate(value)
                        except InvalidToken:
                            counts["undecryptable"] += 1
                            continue
                        res = await db.execute(update(table).where(pk == row[0], table.c[col] == value)
                                               .values({col: new}))
                        # 0 rows: the app saved a new value meanwhile (written with the current key)
                        counts["updated" if res.rowcount else "current"] += 1
            last = rows[-1][0]
        await db.commit()
    return counts


async def reencrypt_all(batch_size: int = 500) -> dict:
    totals = {}
    for table, columns in encrypted_columns():
        counts = await reencrypt_table(table, columns, batch_size=batch_size)
        logger.info("%s (%s): %s", table.name, ", ".join(columns),
                    " ".join(f"{k}={v}" for k, v in counts.items()))
        totals[table.name] = counts
    return totals


async def _main() -> int:
    try:
        totals = await reencrypt_all()
    finally:
        await engine.dispose()
    bad = sum(c["undecryptable"] for c in totals.values())
    if bad:
        logger.warning("%d value(s) could not be decrypted with any configured key and were left unchanged "
                       "(wrong SECRET_KEY/ENCRYPTION_KEY, or a missing ENCRYPTION_KEYS_PREVIOUS entry)", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(asyncio.run(_main()))
