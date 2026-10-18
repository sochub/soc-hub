from tests.audit_fixtures import PW, hdr, rows, run


async def _login(c, email, pw):
    return await c.post("/api/v1/auth/login/access-token", data={"username": email, "password": pw})


def test_login_success_and_failures():
    async def body(c, w):
        assert (await _login(c, w.emails["admin"], PW)).status_code == 200
        ok = await rows(w, action="login.success")
        assert len(ok) == 1 and ok[0].tenant_id == w.ta and ok[0].user_id == w.users["admin"]
        assert ok[0].category == "security" and ok[0].outcome == "success"

        assert (await _login(c, w.emails["admin"], "wrong-password!!")).status_code == 401
        bad = await rows(w, action="login.failed", tenant_id=w.ta)
        assert len(bad) == 1 and bad[0].changes == {"email_domain": "example.com"} and bad[0].user_id is None

        assert (await _login(c, f"nobody@{w.h}.invalid", "CANARYPW-123456")).status_code == 401
        unknown = await rows(w, action="login.failed")
        plat = [r for r in unknown if r.tenant_id is None]
        assert len(plat) == 1 and plat[0].changes == {"email_domain": f"{w.h}.invalid"}
        for r in await rows(w):
            assert "CANARYPW" not in repr(vars(r)) and "wrong-password" not in repr(vars(r))
        # a login is one row, not an extra "write" row
        assert await rows(w, category="write", route="/api/v1/auth/login/access-token") == []
    run(body)


def test_switch_tenant_and_logout():
    async def body(c, w):
        r = await c.post("/api/v1/auth/switch-tenant", json={"tenant_id": w.tb}, headers=hdr(w, "root"))
        assert r.status_code == 200
        sw = await rows(w, action="tenant.switch")
        assert len(sw) == 1 and sw[0].tenant_id == w.tb and sw[0].changes == {"from_tenant_id": w.ta}
        assert (await c.post("/api/v1/auth/logout", headers=hdr(w, "admin"))).status_code == 204
        lo = await rows(w, action="logout")
        assert len(lo) == 1 and lo[0].tenant_id == w.ta and lo[0].user_id == w.users["admin"]
    run(body)


def test_password_change_event():
    async def body(c, w):
        r = await c.post("/api/v1/users/me/password", json={"current_password": PW, "new_password": "Brand-New-Passw0rd!"},
                         headers=hdr(w, "analyst"))
        assert r.status_code == 200, r.text
        ev = await rows(w, action="password.changed")
        assert len(ev) == 1 and ev[0].user_id == w.users["analyst"]
        for row in await rows(w):
            assert "Brand-New-Passw0rd" not in repr(vars(row)) and PW not in repr(vars(row))
    run(body)
