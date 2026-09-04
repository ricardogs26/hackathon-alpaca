"""WhatsApp notifications through the Amael bridge (`POST /send`). Disabled
when no destination is configured; never raises into the caller."""
from __future__ import annotations

import logging

logger = logging.getLogger("optionwright.notify")


def send_whatsapp(text: str) -> bool:
    from optionwright.settings import get_settings

    s = get_settings()
    if not s.whatsapp_to or not s.whatsapp_send_url:
        logger.info("WhatsApp not configured; message kept in logs:\n%s", text)
        return False
    try:
        import httpx

        r = httpx.post(f"{s.whatsapp_send_url.rstrip('/')}/send",
                       json={"phoneNumber": s.whatsapp_to, "text": text}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("WhatsApp send failed: %s", exc)
        return False
