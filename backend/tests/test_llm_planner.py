"""BYOK planner LLM, contract-tested against a local OpenAI-compatible stub (not a real model)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from conftest import signup

MODE = {"bad": False, "seen_auth": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); body = json.loads(self.rfile.read(n))
        MODE["seen_auth"] = self.headers.get("authorization")
        brief = json.loads(body["messages"][1]["content"])["brief"]
        total = float(brief["target_duration"])
        scenes = [{"title": f"S{i + 1}", "description": "d", "prompt": f"stub prompt {i + 1}", "duration": total / 2} for i in range(2)]
        content = "not json" if MODE["bad"] else json.dumps({"story": "stub story", "scenes": scenes})
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_llm_plan_used_with_key_then_fallback_on_garbage(app_client, env):
    srv = _serve()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    cl = app_client(); signup(cl)
    r = cl.post("/api/v1/providers/llm_openai_compat/connect", json={"label": "p", "secret": "sk-test-1234567890", "config": {"base_url": "ftp://x"}})
    assert r.status_code == 422                                         # bad endpoint refused
    cl.ok("post", "/api/v1/providers/llm_openai_compat/connect", json={"label": "p", "secret": "sk-test-1234567890", "config": {"base_url": base, "model": "stub-1"}})
    pid = cl.ok("post", "/api/v1/projects", json={"title": "L", "concept": "rain", "target_duration": 8, "scene_count": 2, "require_storyboard_approval": False})["id"]
    plan = cl.ok("post", f"/api/v1/projects/{pid}/plan", json={"llm": {"model": "stub-1"}})
    assert MODE["seen_auth"] == "Bearer sk-test-1234567890"             # the user's own key reached their endpoint
    assert [s["prompt"] for s in plan["scenes"]] == ["stub prompt 1", "stub prompt 2"]
    assert "sk-test" not in json.dumps(cl.ok("get", "/api/v1/providers"))   # key never returned to the browser
    MODE["bad"] = True
    plan2 = cl.ok("post", f"/api/v1/projects/{pid}/plan", json={"llm": {"model": "stub-1"}})
    assert len(plan2["scenes"]) == 2 and plan2["scenes"][0]["prompt"] != "stub prompt 1"   # heuristic fallback
    srv.shutdown()
