import asyncio, httpx
from app.providers.base import ProviderContext, GenerationRequest, ProviderError
from app.providers import replicate as R

def mk(handler, **cfg):
    p = R.ReplicateProvider(ProviderContext("u", "c", "replicate", config=cfg, secret="tok"))
    orig = p._client
    p._client = lambda: httpx.AsyncClient(base_url=R.BASE, transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer tok"})
    return p

REQ = GenerationRequest("k", "p", "s", 0, "a fox", "", 6, 6, "16:9", 1280, 720, 24)

def test_submit_status_and_402():
    def h(r):
        if r.method == "POST":
            return httpx.Response(402 if r.headers.get("x-fail") else 201, json={"id": "abc"})
        return httpx.Response(200, json={"status": "processing"})
    p = mk(h)
    assert asyncio.run(p.submit_generation(REQ)) == "abc"
    assert asyncio.run(p.get_job_status("abc")).state == "running"
    p2 = mk(lambda r: httpx.Response(402))
    try:
        asyncio.run(p2.submit_generation(REQ)); assert False
    except ProviderError as e:
        assert e.code == "CREDITS_EXHAUSTED"

def test_free_flag():
    p = mk(lambda r: httpx.Response(200), free_trial_only=True)
    assert asyncio.run(p.estimate_cost(REQ)).unit == "runs"
    assert asyncio.run(mk(lambda r: httpx.Response(200)).estimate_cost(REQ)).is_paid is True

def test_catalog_fits_columns():
    from app.database.models import GenerationProvider as G
    from app.providers.catalog_data import CATALOG
    from app.providers.registry import CORE_PROVIDERS
    for r in CORE_PROVIDERS + CATALOG:
        for k, v in r.items():
            col = getattr(G, k).property.columns[0]
            n = getattr(col.type, "length", None)
            assert n is None or len(str(v)) <= n, (r["id"], k, len(str(v)), n)


def test_free_trial_flag_rejected_for_unverified_user(monkeypatch):
    from starlette.testclient import TestClient
    from app.main import app
    monkeypatch.delenv("VGOH_FREE_TRIAL_VERIFIED_EMAILS", raising=False)
    with TestClient(app) as cl:
        r = cl.post("/api/v1/auth/register", json={"email": "ft@example.com", "password": "pw-123456-abc", "name": "t"}, headers={"X-VGOH": "1"})
        assert r.status_code in (200, 201), r.text
        r = cl.post("/api/v1/providers/replicate/connect", json={"label": "x", "secret": "tok-123456789012", "balance": 3, "config": {"free_trial_only": True}}, headers={"X-VGOH": "1"})
        assert r.status_code == 422 and "unverified" in r.text
