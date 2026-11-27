"""Unified API-key store — one place to enter keys for every provider.

A single JSON file holds one entry per provider. This is the one option a user
needs: paste a key for any provider (Runway, fal, Luma, Kling, MiniMax, Hugging
Face, Google Veo, Replicate, …) and every adapter picks it up.

Security posture:
  * The file is written with mode ``0600`` (owner read/write only).
  * If a cipher is configured (``VGOH_CREDENTIAL_KEY`` + ``cryptography``), each
    value is encrypted at rest and the file is marked ``encrypted: true``.
  * Without a cipher the file is written as plaintext JSON but **explicitly
    labelled** so the operator knows to protect it or install ``cryptography``.
    This is a deliberate, visible trade-off — the harness never *pretends* the
    file is encrypted when it is not.
  * Keys are never returned by the API and never logged.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from app.security.credentials import Cipher, CredentialError, resolve_default_cipher

DEFAULT_KEYS_FILE = ".vgoh_keys.json"


class KeyStore:
    """A single-file, per-provider API-key store."""

    def __init__(self, path: str | Path | None = None, cipher: Cipher | None = None) -> None:
        env_path = os.environ.get("VGOH_KEYS_FILE")
        self.path = Path(path or env_path or DEFAULT_KEYS_FILE)
        self.cipher = cipher if cipher is not None else resolve_default_cipher()

    # --- persistence -----------------------------------------------------------
    def _read(self) -> dict:
        if not self.path.exists():
            return {"_meta": {"version": 1, "encrypted": self.cipher is not None}, "keys": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise CredentialError(f"cannot read key store {self.path}: {exc}") from exc

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:  # pragma: no cover - platform dependent
            pass

    # --- public API ------------------------------------------------------------
    @property
    def encrypted(self) -> bool:
        return self.cipher is not None

    def set(self, provider_id: str, secret: str) -> None:
        if not provider_id:
            raise CredentialError("refusing to store a key without a provider id")
        if not secret:
            raise CredentialError("refusing to store an empty key")
        data = self._read()
        data.setdefault("keys", {})
        data["_meta"] = {"version": 1, "encrypted": self.encrypted}
        data["keys"][provider_id] = (
            self.cipher.encrypt(secret) if self.cipher is not None else secret
        )
        self._write(data)

    def set_many(self, keys: dict[str, str]) -> list[str]:
        """Store several keys in one write. Returns the provider ids stored.

        Blank provider ids or blank secrets are skipped (not an error) so a
        single form submit with empty fields is harmless.
        """
        data = self._read()
        data.setdefault("keys", {})
        data["_meta"] = {"version": 1, "encrypted": self.encrypted}
        stored: list[str] = []
        for provider_id, secret in keys.items():
            if not provider_id or not secret:
                continue
            data["keys"][provider_id] = (
                self.cipher.encrypt(secret) if self.cipher is not None else secret
            )
            stored.append(provider_id)
        self._write(data)
        return stored

    def delete(self, provider_id: str) -> bool:
        data = self._read()
        existed = provider_id in data.get("keys", {})
        data.get("keys", {}).pop(provider_id, None)
        self._write(data)
        return existed

    def get(self, provider_id: str) -> str | None:
        value = self._read().get("keys", {}).get(provider_id)
        if value is None:
            return None
        if self.cipher is not None:
            try:
                return self.cipher.decrypt(value)
            except Exception as exc:  # noqa: BLE001 - key mismatch / corruption
                raise CredentialError(
                    f"cannot decrypt key for {provider_id}; wrong VGOH_CREDENTIAL_KEY?"
                ) from exc
        return value

    def configured(self) -> list[str]:
        return sorted(self._read().get("keys", {}).keys())

    def status(self) -> list[dict]:
        """Which providers have a key configured (never the value)."""
        return [
            {"provider_id": pid, "configured": True, "source": "keystore"}
            for pid in self.configured()
        ]

    # --- resolution used by adapters ------------------------------------------
    def resolve(self, provider_id: str, env_var: str = "") -> str | None:
        """Resolve a key: environment first (deployment override), then the store."""
        if env_var:
            env_value = os.environ.get(env_var)
            if env_value:
                return env_value
        try:
            return self.get(provider_id)
        except CredentialError:
            return None

    def source_for(self, provider_id: str, env_var: str = "") -> str | None:
        if env_var and os.environ.get(env_var):
            return f"env:{env_var}"
        try:
            return "keystore" if self.get(provider_id) else None
        except CredentialError:
            return "keystore(unreadable)"


_default_store: KeyStore | None = None
_default_store_path: str | None = None


def default_key_store() -> KeyStore:
    """Process-wide default store (path from ``VGOH_KEYS_FILE``).

    The cache is keyed on the resolved path so a test (or deployment) that
    changes ``VGOH_KEYS_FILE`` gets a fresh store rather than a stale one.
    """
    global _default_store, _default_store_path
    path = os.environ.get("VGOH_KEYS_FILE") or DEFAULT_KEYS_FILE
    if _default_store is None or _default_store_path != path:
        _default_store = KeyStore(path)
        _default_store_path = path
    return _default_store
