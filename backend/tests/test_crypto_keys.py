"""At-rest encryption keys are separated from the JWT signing secret and rotatable, without ever losing
access to data encrypted under the legacy SHA-256(SECRET_KEY) key."""
import asyncio
import base64
import hashlib
import secrets

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select

from app.core.config import settings
from app.utils import crypto


def _legacy_fernet(secret_key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", None, raising=False)
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_PREVIOUS", "", raising=False)
    return monkeypatch


def test_new_ciphertext_roundtrips(keys):
    token = crypto.encrypt("xoxb-secret")
    assert token != "xoxb-secret"
    assert crypto.decrypt(token) == "xoxb-secret"


def test_legacy_sha256_ciphertext_still_decrypts(keys):
    legacy = _legacy_fernet(settings.SECRET_KEY).encrypt(b"old-value").decode()
    assert crypto.decrypt(legacy) == "old-value"


def test_new_ciphertext_is_not_under_the_legacy_key(keys):
    token = crypto.encrypt("v")
    with pytest.raises(InvalidToken):
        _legacy_fernet(settings.SECRET_KEY).decrypt(token.encode())


def test_encryption_key_is_not_the_jwt_secret_hash(keys):
    primary = crypto.current_key()
    assert primary != base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())


def test_encryption_key_override(keys):
    before = crypto.encrypt("from-secret-key-derivation")
    keys.setattr(settings, "ENCRYPTION_KEY", "k" * 48)
    token = crypto.encrypt("with-override")
    assert crypto.decrypt(token) == "with-override"
    # data written before ENCRYPTION_KEY was configured (HKDF of SECRET_KEY) and legacy data still decrypt
    assert crypto.decrypt(before) == "from-secret-key-derivation"
    assert crypto.decrypt(_legacy_fernet(settings.SECRET_KEY).encrypt(b"x").decode()) == "x"
    # and the override really is the key in use: dropping it makes the new ciphertext unreadable
    keys.setattr(settings, "ENCRYPTION_KEY", None)
    with pytest.raises(InvalidToken):
        crypto.decrypt(token)


def test_previous_keys_decrypt_old_data(keys):
    keys.setattr(settings, "ENCRYPTION_KEY", "old-" + "a" * 44)
    old = crypto.encrypt("rotated")
    keys.setattr(settings, "ENCRYPTION_KEY", "new-" + "b" * 44)
    with pytest.raises(InvalidToken):
        crypto.decrypt(old)
    keys.setattr(settings, "ENCRYPTION_KEYS_PREVIOUS", "unrelated-" + "c" * 40 + ",old-" + "a" * 44)
    assert crypto.decrypt(old) == "rotated"
    new = crypto.encrypt("fresh")
    keys.setattr(settings, "ENCRYPTION_KEY", "old-" + "a" * 44)
    keys.setattr(settings, "ENCRYPTION_KEYS_PREVIOUS", "")
    with pytest.raises(InvalidToken):
        crypto.decrypt(new)  # encryption always uses the current key, not a previous one


def test_jwt_signing_still_uses_secret_key(keys):
    import jwt
    from app.core.security import create_access_token
    tok = create_access_token({"sub": "1"})
    assert jwt.decode(tok, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])["sub"] == "1"


def test_reencrypt_covers_every_enc_column():
    from app.scripts.reencrypt_secrets import encrypted_columns
    found = {(t.name, c) for t, cols in encrypted_columns() for c in cols}
    assert found == {
        ("users", "mfa_secret_enc"), ("guest_accounts", "mfa_secret_enc"),
        ("slack_integrations", "bot_token_enc"), ("slack_integrations", "signing_secret_enc"),
        ("slack_integrations", "app_token_enc"), ("tenant_ai_configs", "credentials_enc"),
        ("tenant_sso_configs", "oidc_client_secret_enc"), ("tenant_secrets", "value_enc"),
        ("tenant_enrichment_configs", "credentials_enc"),
    }, found


async def _reencrypt_scenario():
    from app.db.session import AsyncSessionLocal, engine
    from app.models.tenant import Tenant
    from app.models.tenant_secret import TenantSecret
    from app.scripts.reencrypt_secrets import reencrypt_table

    h = secrets.token_hex(3)
    ids = {"t": [], "s": []}
    legacy = _legacy_fernet(settings.SECRET_KEY).encrypt(b"legacy-plain").decode()
    current = crypto.encrypt("current-plain")
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="crypto-test", slug=f"crypto-test-{h}")
            db.add(t)
            await db.flush()
            ids["t"] = [t.id]
            a = TenantSecret(tenant_id=t.id, name="LEGACY_ONE", value_enc=legacy, allowed_hosts=["x.example"])
            b = TenantSecret(tenant_id=t.id, name="CURRENT_ONE", value_enc=current, allowed_hosts=["x.example"])
            c = TenantSecret(tenant_id=t.id, name="GARBAGE_ONE", value_enc="not-a-token", allowed_hosts=["x.example"])
            db.add_all([a, b, c])
            await db.flush()
            ids["s"] = [a.id, b.id, c.id]
            await db.commit()

        table = TenantSecret.__table__
        first = await reencrypt_table(table, ["value_enc"], ids=ids["s"], batch_size=2)
        assert first == {"updated": 1, "current": 1, "undecryptable": 1, "null": 0}, first

        async with AsyncSessionLocal() as db:
            rows = {r.name: r.value_enc for r in (await db.execute(
                select(TenantSecret).where(TenantSecret.id.in_(ids["s"])))).scalars()}
        assert rows["LEGACY_ONE"] != legacy
        assert Fernet(crypto.current_key()).decrypt(rows["LEGACY_ONE"].encode()) == b"legacy-plain"
        assert crypto.decrypt(rows["LEGACY_ONE"]) == "legacy-plain"
        assert rows["CURRENT_ONE"] == current  # already current: untouched
        assert rows["GARBAGE_ONE"] == "not-a-token"  # never destroyed

        second = await reencrypt_table(table, ["value_enc"], ids=ids["s"], batch_size=2)
        assert second == {"updated": 0, "current": 2, "undecryptable": 1, "null": 0}, second
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["s"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
        await engine.dispose()


def test_reencrypt_converts_legacy_row_and_is_idempotent(keys):
    asyncio.run(_reencrypt_scenario())


def _prod_settings(**kw):
    from app.core.config import Settings
    base = dict(SECRET_KEY="x" * 48, DATABASE_URL="postgresql+asyncpg://soc:S3cure-pw@db:5432/sicms",
                REDIS_URL="redis://:S3cure-redis@redis:6379/0", ENVIRONMENT="production")
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_encryption_key_is_optional_in_production():
    assert _prod_settings().ENCRYPTION_KEY is None
    assert _prod_settings(ENCRYPTION_KEY="").ENCRYPTION_KEY == ""  # blank env var = unset, boots
    _prod_settings(ENCRYPTION_KEY="e" * 48)


@pytest.mark.parametrize("value", ["short", "x" * 48])  # too short / same as SECRET_KEY
def test_production_rejects_weak_encryption_key(value):
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        _prod_settings(ENCRYPTION_KEY=value)


def test_old_secret_key_in_previous_keys_decrypts_hkdf_data(keys):
    old_secret = settings.SECRET_KEY
    token = crypto.encrypt("written-under-old-secret-key")  # HKDF(old SECRET_KEY), ENCRYPTION_KEY unset
    legacy = _legacy_fernet(old_secret).encrypt(b"legacy").decode()
    keys.setattr(settings, "SECRET_KEY", "n" * 48)  # rotated
    with pytest.raises(InvalidToken):
        crypto.decrypt(token)
    keys.setattr(settings, "ENCRYPTION_KEYS_PREVIOUS", old_secret)
    assert crypto.decrypt(token) == "written-under-old-secret-key"
    with pytest.raises(InvalidToken):  # documented: the legacy SHA-256 format is not recovered this way
        crypto.decrypt(legacy)
