"""Fake OIDC IdP helpers shared by service and route tests."""
import json
import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.example.com"
CLIENT_ID = "client-123"


def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_for(*keys_kids) -> dict:
    keys = []
    for key, kid in keys_kids:
        d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        d.update({"kid": kid, "use": "sig", "alg": "RS256"})
        keys.append(d)
    return {"keys": keys}


def sign_token(key, kid, *, nonce="n1", aud=CLIENT_ID, iss=ISSUER, **extra) -> str:
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": "u1", "iat": now, "exp": now + 300,
              "nonce": nonce, "email": "alice@corp.com", **extra}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def discovery_doc(issuer=ISSUER) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
    }


def new_kid() -> str:
    return uuid.uuid4().hex[:8]
