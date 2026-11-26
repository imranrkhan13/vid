from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings


def _master_key() -> bytes:
    env = os.environ.get("VGOH_MASTER_KEY")
    if env:
        return hashlib.sha256(env.encode()).digest()
    f: Path = settings.data_dir / "master.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return bytes.fromhex(f.read_text().strip())


def encrypt_secret(plain: str, aad: str) -> str:
    """AES-256-GCM. The AAD ties a ciphertext to its owner, so a row copied to another user fails to decrypt."""
    iv = os.urandom(12)
    ct = AESGCM(_master_key()).encrypt(iv, plain.encode(), aad.encode())
    return json.dumps({"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()})


def decrypt_secret(blob: str, aad: str) -> str:
    o = json.loads(blob)
    return AESGCM(_master_key()).decrypt(base64.b64decode(o["iv"]), base64.b64decode(o["ct"]), aad.encode()).decode()


def hash_password(pw: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(pw.encode(), salt=salt.encode(), n=2 ** 14, r=8, p=1, dklen=64)
    return salt, h.hex()


def verify_password(pw: str, salt: str, expected: str) -> bool:
    _, h = hash_password(pw, salt)
    return hmac.compare_digest(h, expected)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


_SECRET_KEY = re.compile(r"(api[_-]?key|secret|token|password|authorization|bearer)", re.I)
_SECRET_VAL = re.compile(r"(sk-|key-|Bearer\s+)[A-Za-z0-9._\-]{8,}")


def redact(obj):
    if isinstance(obj, str):
        return _SECRET_VAL.sub(r"\1[redacted]", obj)
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: ("[redacted]" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in obj.items()}
    return obj
