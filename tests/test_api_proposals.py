"""Proposal decisions over the API: token-gated, honest errors."""
from fastapi.testclient import TestClient

import optionwright.api.main as m
from optionwright.storage import store


def _client(monkeypatch, token=""):
    for n in ("init_schema", "seed_rules", "get_positions", "get_equity_curve", "last_opened_confidence"):
        monkeypatch.setattr(store, n, lambda *a, **k: [] if n in ("get_positions", "get_equity_curve") else None)
    monkeypatch.setattr(m, "get_settings", lambda: type("S", (), {
        "rules_token": token, "cycle_seconds": 180, "exit_check_seconds": 60, "underlyings_list": ["SPY"],
        "llm_model": "x", "alpaca_paper": True, "learning_cron_utc": ""})())
    monkeypatch.setattr(store, "list_proposals", lambda limit=50: [{"id": 1, "status": "pending"}])
    decided = []
    monkeypatch.setattr(store, "decide_proposal", lambda pid, approve, by: decided.append((pid, approve, by)) or {"id": pid, "status": "approved" if approve else "rejected"})
    return TestClient(m.app), decided


def test_list_proposals_is_public_but_decisions_need_the_token(monkeypatch):
    c, decided = _client(monkeypatch, token="")
    with c:
        assert c.get("/api/rules/proposals").json() == [{"id": 1, "status": "pending"}]
        assert c.post("/api/rules/proposals/1/approve").status_code == 403
    c, decided = _client(monkeypatch, token="secret")
    with c:
        assert c.post("/api/rules/proposals/1/approve").status_code == 401
        r = c.post("/api/rules/proposals/1/approve", headers={"Authorization": "Bearer secret"}, json={"decided_by": "ricardo"})
        assert r.status_code == 200 and decided == [(1, True, "ricardo")]
        assert c.post("/api/rules/proposals/1/reject", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert c.post("/api/rules/proposals/1/maybe", headers={"Authorization": "Bearer secret"}).status_code == 404
