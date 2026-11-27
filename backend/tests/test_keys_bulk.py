"""Unified API-key entry: one place to enter keys for several providers at once."""
from conftest import signup


def _user(app_client, email="keys@example.com"):
    cl = app_client()
    signup(cl, email)
    return cl


def test_key_status_lists_providers_and_never_leaks_secrets(app_client):
    cl = _user(app_client)
    st = cl.ok("get", "/api/v1/keys")
    ids = {p["provider_id"] for p in st["providers"]}
    assert "mock_a" in ids and "manual" in ids
    mock = [p for p in st["providers"] if p["provider_id"] == "mock_a"][0]
    assert mock["is_mock"] is True and mock["requires_key"] is False and mock["has_key"] is False
    assert st["configured_count"] == 0


def test_bulk_entry_stores_several_keys_and_never_echoes_them(app_client):
    cl = _user(app_client)
    secret = "sk-bulk-SUPERSECRET-abcdef123456"
    res = cl.ok("put", "/api/v1/keys/bulk", json={"keys": {"mock_a": secret, "mock_b": secret}})
    assert set(res["stored"]) == {"mock_a", "mock_b"}
    assert res["failed"] == []
    assert secret not in str(res)  # never echoed back

    st = cl.ok("get", "/api/v1/keys")
    assert st["configured_count"] == 2
    mock_a = [p for p in st["providers"] if p["provider_id"] == "mock_a"][0]
    assert mock_a["has_key"] is True
    assert secret not in str(st)

    # stored encrypted at rest, bound to the owner
    from app.database.models import ProviderCredential
    from app.database.session import new_session
    from app.core.security import decrypt_secret
    db = new_session()
    try:
        cred = db.query(ProviderCredential).filter_by(provider_id="mock_a").first()
        assert cred.secret_enc and secret not in cred.secret_enc
        assert decrypt_secret(cred.secret_enc, f"{cred.user_id}|mock_a|{cred.id}") == secret
    finally:
        db.close()


def test_bulk_entry_reports_per_provider_failures_without_aborting(app_client):
    cl = _user(app_client)
    res = cl.ok("put", "/api/v1/keys/bulk", json={"keys": {"mock_a": "sk-good-1234567890", "does_not_exist": "sk-x-1234567890"}})
    assert res["stored"] == ["mock_a"]
    assert [f["provider_id"] for f in res["failed"]] == ["does_not_exist"]


def test_single_key_endpoint_and_delete(app_client):
    cl = _user(app_client)
    r = cl.ok("put", "/api/v1/keys", json={"provider_id": "mock_b", "api_key": "sk-one-1234567890"})
    assert r["has_key"] is True and "sk-one" not in str(r)
    assert cl.ok("get", "/api/v1/keys")["configured_count"] == 1
    d = cl.ok("delete", "/api/v1/keys/mock_b")
    assert d["removed"] == 1
    assert cl.ok("get", "/api/v1/keys")["configured_count"] == 0


def test_keys_are_private_to_the_account(app_client):
    a = _user(app_client, "keys-a@example.com")
    b = _user(app_client, "keys-b@example.com")
    a.ok("put", "/api/v1/keys", json={"provider_id": "mock_a", "api_key": "sk-a-1234567890"})
    assert a.ok("get", "/api/v1/keys")["configured_count"] == 1
    assert b.ok("get", "/api/v1/keys")["configured_count"] == 0
