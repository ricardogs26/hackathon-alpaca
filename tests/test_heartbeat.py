from optionwright.agent.heartbeat import decide


def test_unreachable_agent_alerts():
    assert "no responde" in decide(None, None)


def test_scheduler_down_alerts_even_when_closed():
    assert "scheduler" in decide({"scheduler_running": False, "market_open": False}, None)


def test_market_closed_is_quiet():
    assert decide({"scheduler_running": True, "market_open": False}, None) is None


def test_market_open_needs_cycles():
    assert decide({"scheduler_running": True, "market_open": True, "version": "0.9.1"}, 21.0) is None
    txt = decide({"scheduler_running": True, "market_open": True, "version": "0.9.1"}, 0.0)
    assert "CERO ciclos" in txt and "0.9.1" in txt


def test_prometheus_down_is_a_soft_warning():
    assert "Prometheus" in decide({"scheduler_running": True, "market_open": True}, None)
