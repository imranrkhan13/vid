"""Credential storage with pluggable encryption and redaction (sections 27, 30).

Rules enforced here:
  * Secrets are **never** stored in plaintext. Without a configured cipher the
    store refuses to persist a secret rather than falling back to plaintext.
  * Secrets are never logged, never returned through the API, and are redacted
    from any string before it is written to a log or event.
  * Every store/read/delete is written to the audit log (without the secret).

The default cipher is Fernet (symmetric, authenticated). ``cryptography`` is an
optional dependency, imported lazily; if it is absent, ``resolve_default_cipher``
returns ``None`` and the store stays inert for secrets.
"""

from __future__ import annotations

import abc
import base64
import os
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.generation import AuditLog
from app.models.providers import ProviderCredential


class CredentialError(Exception):
    """Raised for credential operations that would be unsafe."""


class Cipher(abc.ABC):
    """Symmetric encryption for credential material."""

    name: str = "abstract"

    @abc.abstractmethod
    def encrypt(self, plaintext: str) -> str: ...

    @abc.abstractmethod
    def decrypt(self, ciphertext: str) -> str: ...


class FernetCipher(Cipher):
    """Authenticated symmetric encryption via ``cryptography.Fernet``."""

    name = "fernet"

    def __init__(self, key: str | bytes | None = None) -> None:
        try:
            from cryptography.fernet import Fernet  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "cryptography is not installed; cannot use FernetCipher"
            ) from exc
        if key is None:
            key = os.environ.get("VGOH_CREDENTIAL_KEY")
        if not key:
            raise CredentialError("no credential key configured (set VGOH_CREDENTIAL_KEY)")
        if isinstance(key, str):
            key = key.encode()
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


class InsecureTestCipher(Cipher):
    """Reversible base64 obfuscation for tests only.

    Explicitly *not* encryption. It exists so tests can exercise the store
    without the ``cryptography`` dependency, and it must be opted into by name.
    """

    name = "insecure-test"

    def encrypt(self, plaintext: str) -> str:
        return "insecure:" + base64.b64encode(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith("insecure:"):
            raise CredentialError("not an InsecureTestCipher payload")
        return base64.b64decode(ciphertext[len("insecure:") :]).decode()


def resolve_default_cipher() -> Cipher | None:
    """Return the production cipher, or ``None`` if none can be configured."""
    try:
        return FernetCipher()
    except (RuntimeError, CredentialError):
        return None


# --- redaction -------------------------------------------------------------

def redact(text: str, secrets: list[str]) -> str:
    """Replace every occurrence of each secret with a fixed mask."""
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***REDACTED***")
    return out


def redact_dict(payload: dict, secrets: list[str]) -> dict:
    """Recursively redact secret values inside a JSON-like structure."""
    def _walk(value: object) -> object:
        if isinstance(value, str):
            return redact(value, secrets)
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    result = _walk(payload)
    return result if isinstance(result, dict) else {}


class CredentialStore:
    """Encrypted credential persistence + audit, keyed by provider."""

    def __init__(self, session: Session, cipher: Cipher | None) -> None:
        self.session = session
        self.cipher = cipher

    def store(
        self,
        provider_id: str,
        secret: str,
        *,
        actor: str = "system",
        billing_account: str = "",
        organization: str = "",
    ) -> ProviderCredential:
        if self.cipher is None:
            raise CredentialError(
                "no cipher configured; refusing to store a credential in plaintext"
            )
        row = ProviderCredential(
            id=f"cred-{uuid.uuid4().hex[:12]}",
            provider_id=provider_id,
            billing_account=billing_account,
            organization=organization,
            encrypted_secret=self.cipher.encrypt(secret),
        )
        self.session.add(row)
        self._audit(actor, "credential.store", provider_id)
        self.session.flush()
        return row

    def reveal(self, credential_id: str, *, actor: str = "system") -> str:
        row = self.session.get(ProviderCredential, credential_id)
        if row is None:
            raise CredentialError(f"unknown credential {credential_id!r}")
        if self.cipher is None:
            raise CredentialError("no cipher configured; cannot decrypt credential")
        self._audit(actor, "credential.reveal", row.provider_id)
        return self.cipher.decrypt(row.encrypted_secret)

    def delete(self, credential_id: str, *, actor: str = "system") -> None:
        row = self.session.get(ProviderCredential, credential_id)
        if row is None:
            return
        provider_id = row.provider_id
        self.session.delete(row)
        self._audit(actor, "credential.delete", provider_id)
        self.session.flush()

    def list_for_provider(self, provider_id: str) -> list[ProviderCredential]:
        return list(
            self.session.execute(
                select(ProviderCredential).where(ProviderCredential.provider_id == provider_id)
            ).scalars().all()
        )

    def _audit(self, actor: str, action: str, target: str) -> None:
        # The secret is deliberately absent from the audit detail.
        self.session.add(
            AuditLog(actor=actor, action=action, target=target, detail="")
        )
