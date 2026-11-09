from app.utils.hostguard import validate_outbound_host


def _r(ip):
    return lambda h: [(2, 1, 6, "", (ip, 0))]


def test_cgnat_rejected():
    assert validate_outbound_host("x.example", [], "MAILBOX_PRIVATE_HOSTS", _r("100.64.0.1"))


def test_cgnat_allowlisted():
    assert validate_outbound_host("x.example", ["x.example"], "MAILBOX_PRIVATE_HOSTS", _r("100.64.0.1")) is None


def test_public_allowed():
    assert validate_outbound_host("x.example", [], "MAILBOX_PRIVATE_HOSTS", _r("93.184.216.34")) is None
