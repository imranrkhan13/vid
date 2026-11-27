"""DNS-rebinding pinning and secret-leak canaries."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.core import netguard
from conftest import signup

CANARY = "sk-canary-ZZ9911-do-not-leak"


def test_request_time_resolution_is_validated_and_pinned(monkeypatch):
    monkeypatch.delenv("VGOH_ALLOW_PRIVATE_PROVIDER_HOSTS", raising=False)
    seen = {}

    class Fake(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen["host"], seen["hdr"], seen["sni"] = request.url.host, request.headers["host"], request.extensions.get("sni_hostname")
            return httpx.Response(200, json={"ok": True})

    def run(answer):
        monkeypatch.setattr(netguard.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (answer, 443))])
        t = netguard.PinnedTransport(); t._inner = Fake()

        async def go():
            async with httpx.AsyncClient(transport=t) as cl:
                return await cl.get("https://api.example.test/v1/x")
        return asyncio.run(go())

    assert run("93.184.216.34").status_code == 200                       # public answer is used, pinned
    assert seen == {"host": "93.184.216.34", "hdr": "api.example.test", "sni": b"api.example.test"}
    for bad in ("127.0.0.1", "169.254.169.254", "10.1.2.3", "::1"):      # the answer changed after validation: refused at connect time
        with pytest.raises(netguard.BlockedAddress):
            run(bad)


class Echo(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = ("bad request, you sent " + str(self.headers.get("authorization"))).encode()   # a hostile or sloppy service echoing the key
        self.send_response(400); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    do_GET = do_POST


def test_echoed_secret_is_scrubbed_from_provider_errors_and_planner_note(app_client, env):
    from app.providers.base import ProviderContext, ProviderError
    from app.providers.registry import build_provider
    srv = HTTPServer(("127.0.0.1", 0), Echo); threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    ctx = ProviderContext(user_id="u", credential_id="c", provider_id="custom_http", config={"base_url": base}, secret=CANARY)
    prov = build_provider("custom_http", ctx)
    health = asyncio.run(prov.check_health())
    assert CANARY not in json.dumps(health)
    with pytest.raises(ProviderError) as ei:
        asyncio.run(prov.get_quota())
    assert "400" in ei.value.message and CANARY not in str(ei.value) and CANARY not in ei.value.message
    # planner path: error note shown to the user must not contain the key either
    cl = app_client(); signup(cl)
    cl.ok("post", "/api/v1/providers/llm_openai_compat/connect", json={"label": "p", "secret": CANARY, "config": {"base_url": base, "model": "m"}})
    pid = cl.ok("post", "/api/v1/projects", json={"title": "L", "concept": "rain", "target_duration": 8, "scene_count": 2, "require_storyboard_approval": False})["id"]
    plan = cl.ok("post", f"/api/v1/projects/{pid}/plan", json={"llm": {"model": "m"}})
    everything = json.dumps(plan) + json.dumps(cl.ok("get", f"/api/v1/projects/{pid}")) + json.dumps(cl.ok("get", f"/api/v1/projects/{pid}/events")) + json.dumps(cl.ok("get", "/api/v1/providers"))
    assert CANARY not in everything
    srv.shutdown()


def test_uncaught_exception_text_never_reaches_response_or_logs(app_client, env, caplog, monkeypatch):
    import logging
    from app.api import routes
    from app.providers.base import register_secret
    register_secret(CANARY)
    cl = app_client(); signup(cl)

    def boom(*a, **k):
        raise RuntimeError(f"upstream said Authorization: Bearer {CANARY}")
    monkeypatch.setattr(routes, "project_of", boom)
    with caplog.at_level(logging.DEBUG):
        try:
            r = cl.get("/api/v1/projects/prj_x")
            assert CANARY not in r.text and r.status_code >= 400
        except RuntimeError:
            pass   # TestClient re-raises server errors; the response body was already the generic 500 handler's
    assert CANARY not in caplog.text
    logging.getLogger("vgoh.test").error("leaked? %s", CANARY)
    assert CANARY not in caplog.text
    try:
        raise ValueError(CANARY)
    except ValueError:
        logging.getLogger("vgoh.test").exception("failed")
    assert CANARY not in caplog.text
    from app.workflows import engine as E
    from app.database.session import new_session
    db = new_session()
    ev = E.emit(db, "prj_none", "x", f"echo {CANARY}", {"k": CANARY})
    from app.database.models import WorkflowEvent
    row = db.get(WorkflowEvent, ev)
    assert CANARY not in row.message and CANARY not in json.dumps(row.data)
    db.close()
