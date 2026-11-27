"""Outbound-URL defense for user-supplied provider/planner endpoints (production setting: private hosts NOT allowed)."""
import pytest
from app.providers.base import ProviderError
from app.providers.custom_http import validate_base_url


@pytest.fixture(autouse=True)
def _prod(monkeypatch):
    monkeypatch.delenv("VGOH_ALLOW_PRIVATE_PROVIDER_HOSTS", raising=False)


@pytest.mark.parametrize("url", [
    "http://example.com/v1",                       # plain http
    "https://127.0.0.1/v1", "https://localhost/v1", "https://[::1]/v1",
    "https://169.254.169.254/latest/meta-data",    # cloud metadata
    "https://10.0.0.5/v1", "https://192.168.1.1/v1", "https://172.16.0.9/v1",
    "https://0.0.0.0/v1", "https://[::ffff:127.0.0.1]/v1",
    "ftp://example.com/v1", "file:///etc/passwd", "https:///v1",
])
def test_private_and_odd_urls_refused(url):
    with pytest.raises(ProviderError):
        validate_base_url(url)


def test_planner_and_custom_connect_refuse_private_endpoint(app_client, env, monkeypatch):
    from conftest import signup
    monkeypatch.delenv("VGOH_ALLOW_PRIVATE_PROVIDER_HOSTS", raising=False)
    cl = app_client(); signup(cl)
    r = cl.post("/api/v1/providers/llm_openai_compat/connect", json={"label": "x", "secret": "sk-test-1234567890", "config": {"base_url": "https://169.254.169.254/v1"}})
    assert r.status_code == 422
    r = cl.post("/api/v1/providers/custom_http/connect", json={"label": "x", "secret": "sk-test-1234567890", "config": {"base_url": "https://127.0.0.1/v1"}})
    assert r.status_code == 422
