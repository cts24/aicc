"""Google Calendar appointment booking."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


async def book_sales_appointment(lead: dict, call_id: str, agent_name: str, cfg) -> None:
    if not cfg.google_calendar_id:
        return
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            cfg.google_service_account_file,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        service = build("calendar", "v3", credentials=creds)

        pkt = timezone(timedelta(hours=5))
        now = datetime.now(pkt)
        start = now.replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        end = start + timedelta(minutes=cfg.appointment_duration_minutes)

        name  = lead.get("name") or "Unknown Caller"
        phone = lead.get("phone") or "N/A"
        dest  = lead.get("destination") or "—"
        trip  = lead.get("trip_type") or "—"
        score = lead.get("lead_temperature", "?")

        event = {
            "summary": f"Sales Callback — {name} — {trip}",
            "description": (
                f"Caller: {name}\n"
                f"Phone: {phone}\n"
                f"Destination: {dest}\n"
                f"Trip Type: {trip}\n"
                f"Travel Dates: {lead.get('travel_dates') or '—'}\n"
                f"Group Size: {lead.get('group_size') or '—'}\n"
                f"Budget: {lead.get('budget_range') or '—'}\n"
                f"Lead Score: {score}/10\n"
                f"Summary: {lead.get('summary') or '—'}\n\n"
                f"Booked by: {agent_name} AI — Call ID {call_id}"
            ),
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Karachi"},
            "end":   {"dateTime": end.isoformat(),   "timeZone": "Asia/Karachi"},
        }

        def _create():
            return service.events().insert(calendarId=cfg.google_calendar_id, body=event).execute()

        loop = asyncio.get_event_loop()
        created = await loop.run_in_executor(None, _create)
        log.info(f"[{call_id}] Calendar: sales appointment booked → {created.get('htmlLink', '')}")
    except ImportError:
        log.warning(f"[{call_id}] google-api-python-client not installed")
    except Exception as e:
        log.warning(f"[{call_id}] Calendar booking failed: {e}")
