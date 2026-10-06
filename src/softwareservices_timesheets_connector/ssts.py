"""
Het handtekeningschema SSTS1 (SPEC 5.1 §5), in beide richtingen hetzelfde als app/odoo/module_sign.py aan de kant van de dienst:

  canonical = "SSTS1" \\n direction \\n "POST" \\n path \\n timestamp \\n nonce \\n sha256_hex(body)
  signature = hex(HMAC-SHA256(secret, canonical))

Puur Python, zonder ORM: de nonce-opslag geeft de aanroeper mee (seen_nonce).
"""
import base64
import hashlib
import hmac
import secrets
import time

VERSION = 'SSTS1'
MAX_SKEW = 300
TO_ODOO, TO_APP = 'app->odoo', 'odoo->app'


class SignatureError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code                      # unknown_key, clock_skew, replay, bad_signature


def secret_bytes(secret_b64):
    return base64.b64decode(secret_b64)


def new_secret_b64():
    return base64.b64encode(secrets.token_bytes(32)).decode()


def canonical(direction, path, timestamp, nonce, body):
    return '\n'.join([VERSION, direction, 'POST', path, str(timestamp), nonce, hashlib.sha256(body).hexdigest()])


def signature(secret, direction, path, timestamp, nonce, body):
    return hmac.new(secret, canonical(direction, path, timestamp, nonce, body).encode(), hashlib.sha256).hexdigest()


def headers(secret, key_id, direction, path, body, now=None):
    ts = str(int(now if now is not None else time.time()))
    nonce = secrets.token_hex(16)
    out = {'X-SS-Timestamp': ts, 'X-SS-Nonce': nonce, 'X-SS-Signature': signature(secret, direction, path, ts, nonce, body)}
    if key_id:
        out['X-SS-Key-Id'] = key_id
    return out


def verify(secret, direction, path, body, hdrs, seen_nonce=None, now=None):
    """In de volgorde van SPEC 5.1 §5: tijd, handtekening, en pas dan de nonce (bewaard vóór de verwerking). Wie het geheim
    niet kent, laat dus geen nonce achter (beveiligingsreview 8.3, M5/I7). Gooit SignatureError."""
    ts, nonce, sig = hdrs.get('X-SS-Timestamp'), hdrs.get('X-SS-Nonce'), hdrs.get('X-SS-Signature')
    if not (ts and nonce and sig) or not ts.isdigit() or len(nonce) != 32:
        raise SignatureError('bad_signature')
    if abs((now if now is not None else time.time()) - int(ts)) > MAX_SKEW:
        raise SignatureError('clock_skew')
    if not hmac.compare_digest(sig, signature(secret, direction, path, ts, nonce, body)):
        raise SignatureError('bad_signature')
    if seen_nonce and seen_nonce(nonce):
        raise SignatureError('replay')
    return True
