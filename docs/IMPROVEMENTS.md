# Improvement backlog

Findings from the 2026-09-01 code review, ordered by priority. Items marked
**done** shipped in 0.2.0; the rest are deliberately deferred past the judged
week so the running agent is not touched two days before the snapshot.

## Done in 0.2.0
- Single source of version (`__version__`), exposed in `/health`, `/api/status`
  and the dashboard; `make release`; git tags; CHANGELOG.
- CI (`ruff` + `pytest`) on push/PR.
- Docs aligned with the running system (model, underlyings, gate count, tests,
  exits, `.env.example`); removed the false MCP-server claim.
- Removed unused Redis wiring, the orphan `positions_opened_total` counter,
  dead dashboard code.

## P0 · strategy defined in three places
- `RuleSet` and `ExitParams` dataclass defaults still describe the OLD strategy
  (e.g. take-profit 0.60, 3 open positions, cooldown 3600) while `settings.py`
  and the k8s env carry the current one. Build them from settings
  (`RuleSet.from_settings()`) and drop the divergent defaults.
- The confidence gate lives in `agent/loop.py`; every other gate is in
  `policy/gates.py`. Move it into the engine so "the gates" are one place.

## P1 · tests
- `agent/runner.py` (`manage_positions`, `run_once`) — the only code that
  closes positions with money — has no test. Cover it with fakes.
- Chain/bars caches are only verified by hand in the pod; add a unit test with a
  fake client that counts fetches.
- `api/main.py` endpoints and the `_cached` TTL.
- The Ollama fallback path has never fired in production; add a test that
  forces the primary to fail.

## P1 · structure and style
- `api/dashboard.py` is ~380 lines of HTML/JS inside a Python string; move it
  to `static/dashboard.html`.
- Cache state in `broker/alpaca.py` is module globals; a small `ChainCache`
  class would be unit-testable.
- Spanish/English mix in code (`perception.py`, `store.py`, `loop.py`,
  `analyzer.py`, `settings.py`); pick one language for identifiers and docs.
- `logging.basicConfig` runs at import time in `api/main.py`.
- Seven broad `except Exception` blocks: most are deliberate degradation; make
  each one say so or narrow the type.
- `scripts/` probes are stale (SPY only, old params); update to `settings` or
  archive.

## P2 · correctness and honesty of data
- Realized P&L is computed from the mid at close time, not the actual fill.
  Reconcile against Alpaca fills / account activities, or label it "estimated".
- Swagger (`/docs`) is public on the demo URL; read-only, but decide on purpose.
