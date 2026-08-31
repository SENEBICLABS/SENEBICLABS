"""
Step 3 (manual integration): the authenticated clinician click-through, driven
through a real browser against a LOCALLY-running workforce dev server.

Unlike the live-surface smoke tests, this one needs local orchestration, so it
SKIPS itself unless everything it needs is present:
  - the workforce dev server on http://localhost:3000  (npm run dev)
  - the workforce repo's .env.local (JWT_SECRET) and this repo's .env

It sets up an ISOLATED, webhook-free LS project + pool + throwaway clinician,
drives dashboard -> Start reviewing -> pick a verdict -> Submit review in
Chromium, asserts a task_completion landed, then removes every fixture.

Run:  (terminal 1) cd ../workforce/... && npm run dev
      (terminal 2) PYTHONPATH=. pytest tests/e2e/test_clinician_clickthrough.py
"""
import base64, hashlib, hmac, json, os, secrets, sys, time, urllib.request

import pytest

BASE = "http://localhost:3000"
HEALTH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF = os.path.join(os.path.dirname(HEALTH), "workforce", "senebiclabs-workforce-platform")


def _server_up():
    try:
        urllib.request.urlopen(BASE + "/login", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_server_up() and os.path.exists(f"{WF}/.env.local")),
    reason="needs the workforce dev server on localhost:3000 (npm run dev) + its .env.local",
)


def _envv(path, key):
    for line in open(path):
        if line.startswith(key + "="):
            return line.rstrip("\n").split("=", 1)[1]
    raise KeyError(key)


def test_clinician_clickthrough():
    import httpx
    from playwright.sync_api import sync_playwright
    sys.path.insert(0, HEALTH)
    from app.services import labelstudio as lsvc

    SB = _envv(f"{HEALTH}/.env", "SUPABASE_URL").rstrip("/")
    SBK = _envv(f"{HEALTH}/.env", "SUPABASE_SERVICE_KEY")
    LS = _envv(f"{HEALTH}/.env", "LS_URL").rstrip("/")
    LST = _envv(f"{HEALTH}/.env", "LS_TOKEN")
    JWT = _envv(f"{WF}/.env.local", "JWT_SECRET")
    sbh = {"apikey": SBK, "Authorization": f"Bearer {SBK}", "Content-Type": "application/json"}
    lsh = {"Authorization": f"Token {LST}"}

    def sb(method, path, body=None, prefer=None):
        h = dict(sbh)
        if prefer:
            h["Prefer"] = prefer
        return httpx.request(method, f"{SB}/rest/v1/{path}", headers=h, json=body, timeout=30)

    cfg = {"purpose": "evaluate", "input": "text", "title": "UI click-through test",
           "schema": {"input": "text",
                      "context": [{"key": "prompt", "label": "Vignette"},
                                  {"key": "output", "label": "Model output"}],
                      "fields": {"verdict": {"type": "single", "options": ["correct", "incorrect"],
                                             "required": True, "label": "Verdict"}}}}
    LP = POOL = CID = None
    try:
        # ── isolated, webhook-free LS project + tasks ──
        LP = httpx.post(f"{LS}/api/projects/", headers=lsh,
                        json={"title": "UI click-through test", "label_config": lsvc.build_label_config(cfg),
                              "maximum_annotations": 1}, timeout=30).json()["id"]
        httpx.post(f"{LS}/api/projects/{LP}/import", headers=lsh,
                   json=[{"data": {"prompt": "71F, cough. RLL opacity.", "output": "Left lower lobe consolidation."}},
                         {"data": {"prompt": "58M, pre-op, clear lungs.", "output": "Normal chest radiograph."}}],
                   timeout=30)
        wh = httpx.get(f"{LS}/api/webhooks/?project={LP}", headers=lsh, timeout=30).json()
        wh = wh if isinstance(wh, list) else wh.get("results", [])
        assert len(wh) == 0, "test LS project must be webhook-free"

        # ── pool + throwaway clinician + eligibility ──
        POOL = sb("POST", "pools", {"name": "UI click-through test", "ls_project_id": int(LP),
                                    "calibration_items": [], "open_access": True, "maximum_annotations": 1,
                                    "eval_config": cfg}, prefer="return=representation").json()[0]["id"]
        email = f"ui-tester-{int(time.time())}@senebiclabs.com"
        CID = sb("POST", "clinicians", {"name": "UI Tester", "email": email, "can_invite": False,
                                        "access_code": secrets.token_hex(8)},
                 prefer="return=representation").json()[0]["id"]
        sb("POST", "pool_eligibility", {"clinician_id": CID, "pool_id": POOL, "eligible": True,
                                        "eligible_since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
           prefer="return=minimal")

        # ── mint the clinician's session cookie (local JWT_SECRET) ──
        def b64(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
        t = int(time.time())
        hdr = b64(json.dumps({"alg": "HS256"}, separators=(",", ":")).encode())
        pl = b64(json.dumps({"clinicianId": CID, "email": email, "iat": t, "exp": t + 3600},
                            separators=(",", ":")).encode())
        tok = f"{hdr}.{pl}.{b64(hmac.new(JWT.encode(), f'{hdr}.{pl}'.encode(), hashlib.sha256).digest())}"

        # ── drive the browser ──
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(base_url=BASE)
            ctx.add_cookies([{"name": "sessionToken", "value": tok, "domain": "localhost", "path": "/"}])
            pg = ctx.new_page()
            pg.goto("/dashboard", wait_until="networkidle")
            assert pg.get_by_text("UI click-through test").first.is_visible()
            pg.get_by_role("button", name="Start reviewing").first.click()
            pg.wait_for_url("**/workspace**")
            radio = pg.locator('input[type=radio][value="correct"]').first
            radio.wait_for(state="visible", timeout=15000)
            radio.check()
            pg.get_by_role("button", name="Submit review").click()
            pg.wait_for_timeout(2500)
            body = pg.inner_text("body").lower()
            assert "could not save" not in body and "unauthorized" not in body
            b.close()

        comps = sb("GET", f"task_completions?pool_id=eq.{POOL}&clinician_id=eq.{CID}&select=ls_task_id").json()
        assert isinstance(comps, list) and len(comps) == 1, f"expected 1 completion, got {comps}"
    finally:
        if LP is not None:
            httpx.delete(f"{LS}/api/projects/{LP}/", headers=lsh, timeout=30)
        if POOL is not None:
            sb("DELETE", f"task_completions?pool_id=eq.{POOL}", prefer="return=minimal")
            sb("DELETE", f"pool_eligibility?pool_id=eq.{POOL}", prefer="return=minimal")
            sb("DELETE", f"pools?id=eq.{POOL}", prefer="return=minimal")
        if CID is not None:
            sb("DELETE", f"clinicians?id=eq.{CID}", prefer="return=minimal")
