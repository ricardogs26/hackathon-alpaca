from optionwright.agent import notify


def test_whatsapp_is_off_without_a_destination(monkeypatch):
    monkeypatch.setattr(notify, "get_settings", lambda: type("S", (), {"whatsapp_to": "", "whatsapp_send_url": "http://x"})(), raising=False)
    import optionwright.settings as st
    monkeypatch.setattr(st, "get_settings", lambda: type("S", (), {"whatsapp_to": "", "whatsapp_send_url": "http://x"})())
    assert notify.send_whatsapp("hola") is False


def test_whatsapp_send_failure_is_swallowed(monkeypatch):
    import optionwright.settings as st
    monkeypatch.setattr(st, "get_settings", lambda: type("S", (), {"whatsapp_to": "521", "whatsapp_send_url": "http://127.0.0.1:9"})())
    assert notify.send_whatsapp("hola") is False
