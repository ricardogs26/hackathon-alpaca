"""
External heartbeat (tech-debt 6.3): does the agent still cycle? Run OUTSIDE the
agent's process (a CronJob every 10 minutes) because a hung scheduler cannot
report itself and the cluster watchdog only checks replicas.

Two questions, in order:
  1. does the API answer at all?            -> no: "agent unreachable"
  2. with the market open, did the entries
     counter move in the last 15 minutes?   -> no: "no cycles"
The decision is a pure function; the job wires HTTP and Prometheus to it.
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.parse
import urllib.request

logger = logging.getLogger("optionwright.heartbeat")

WINDOW = "15m"
QUERY = f"sum(increase(optionwright_cycles_total[{WINDOW}]))"


def decide(status: dict | None, cycles_15m: float | None) -> str | None:
    """The alert text, or None when everything is fine."""
    if status is None:
        return "🚨 optionwright: el agente no responde (/api/status sin respuesta)."
    if not status.get("scheduler_running", True):
        return "🚨 optionwright: el scheduler no está corriendo."
    if status.get("market_open") is not True:
        return None
    if cycles_15m is None:
        return "⚠️ optionwright: Prometheus no contestó; no puedo confirmar que el agente cicle."
    if cycles_15m <= 0:
        return (f"🚨 optionwright: mercado abierto y CERO ciclos en {WINDOW} "
                f"(versión {status.get('version')}). Revisa el pod: kubectl logs -n hackathon deploy/optionwright")
    return None


def _get_json(url: str, timeout: float = 8.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def check(status_url: str, prom_url: str) -> str | None:
    try:
        status = _get_json(status_url)
    except Exception as exc:
        logger.warning("status unreachable: %s", exc)
        status = None
    cycles = None
    if status is not None and status.get("market_open") is True:
        try:
            res = _get_json(prom_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": QUERY}))
            vals = res.get("data", {}).get("result", [])
            cycles = float(vals[0]["value"][1]) if vals else 0.0
        except Exception as exc:
            logger.warning("prometheus unreachable: %s", exc)
    return decide(status, cycles)


def main() -> int:
    from optionwright.agent import notify
    from optionwright.settings import get_settings

    s = get_settings()
    text = check(s.heartbeat_status_url, s.heartbeat_prometheus_url)
    if text is None:
        print("ok")
        return 0
    print(text)
    notify.send_whatsapp(text)
    return 1


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    sys.exit(main())
