from app.utils.crypto import encrypt, decrypt


def test_roundtrip():
    token = encrypt("xoxb-secret")
    assert token != "xoxb-secret"
    assert decrypt(token) == "xoxb-secret"


def test_ciphertexts_differ_per_call():
    assert encrypt("a") != encrypt("a")
