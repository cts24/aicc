"""ntfy push notification sender."""
import logging
import httpx

log = logging.getLogger(__name__)


async def send_ntfy_notification(lead: dict, call_id: str, agent_name: str, cfg) -> None:
    topic = cfg.ntfy_topic
    server = cfg.ntfy_server
    if not topic:
        return
    try:
        temp = lead.get('lead_temperature', '?')
        name = lead.get('name') or 'Unknown'
        stall = lead.get('stall_interest')
        body = (
            f"Name: {name}\n"
            f"Phone: {lead.get('phone') or '—'}\n"
            f"Inquiry: {lead.get('inquiry_type') or '—'}\n"
            f"Stall Interest: {'Yes' if stall is True else 'No' if stall is False else '—'}\n"
            f"Complaint: {lead.get('complaint_details') or '—'}\n"
            f"Score: {temp}/10\n\n"
            f"{lead.get('summary') or ''}"
        )
        priority = "high" if isinstance(temp, int) and temp >= 7 else "default"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{server}/{topic}",
                content=body.encode("utf-8"),
                headers={
                    "Title": f"New Lead - {agent_name} - {name}",
                    "Priority": priority,
                    "Tags": "telephone,globe_with_meridians",
                }
            )
        log.info(f"[{call_id}] ntfy sent")
    except Exception as e:
        log.warning(f"[{call_id}] ntfy failed: {e}")
