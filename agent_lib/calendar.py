"""Cal.com appointment booking via cal_api.py HTTP microservice."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from .config import AgentConfig

log = logging.getLogger(__name__)

CAL_API_BASE = "http://127.0.0.1:8099"
HTTP_TIMEOUT = 10


async def _api_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(f"{CAL_API_BASE}{path}")
        r.raise_for_status()
        return r.json()


async def _api_post(path: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.post(f"{CAL_API_BASE}{path}", json=data)
        r.raise_for_status()
        return r.json()


async def get_event_types() -> list[dict]:
    data = await _api_get("/event-types")
    return data if isinstance(data, list) else []


async def get_available_slots(event_type_id: int, date_str: str) -> list[str]:
    data = await _api_get(f"/slots?event_type_id={event_type_id}&date={date_str}")
    return data.get("slots", [])


async def create_booking(
    event_type_id: int,
    start_time_str: str,
    attendee_name: str,
    attendee_email: str,
    attendee_phone: str = "",
    title: str = "",
) -> dict:
    data = {
        "event_type_id": event_type_id,
        "start_time": start_time_str,
        "name": attendee_name,
        "email": attendee_email,
        "phone": attendee_phone,
        "title": title,
    }
    return await _api_post("/bookings", data)


async def get_bookings_by_email(email: str) -> list[dict]:
    data = await _api_get(f"/bookings?email={email}")
    return data.get("bookings", [])


async def get_bookings_by_phone(phone: str) -> list[dict]:
    encoded = phone.replace("+", "%2B")
    data = await _api_get(f"/bookings?phone={encoded}")
    return data.get("bookings", [])


async def cancel_booking(booking_uid: str) -> bool:
    try:
        result = await _api_post(f"/bookings/{booking_uid}/cancel", {})
        return result.get("status") == "cancelled"
    except Exception as e:
        log.error(f"Cancel booking failed: {e}")
        return False


async def reschedule_booking(booking_uid: str, new_start_time_str: str) -> dict:
    return await _api_post(f"/bookings/{booking_uid}/reschedule", {"start_time": new_start_time_str})


def format_slots_for_prompt(slots: list, date_str: str) -> str:
    if not slots:
        return f"No available slots on {date_str}."
    times = []
    for s in slots:
        dt = datetime.fromisoformat(s)
        times.append(dt.strftime("%I:%M %p").lstrip("0"))
    return f"Available slots on {date_str}: {', '.join(times)}"


async def book_sales_appointment(lead: dict, call_id: str, agent_name: str, cfg: AgentConfig) -> dict:
    name = lead.get("name") or "Unknown Caller"
    phone = lead.get("phone") or ""
    email = lead.get("email") or "caller@psba.gov.pk"

    pkt = timezone(timedelta(hours=5))
    now = datetime.now(pkt)
    start = now.replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while start.weekday() >= 5:
        start += timedelta(days=1)

    result = await create_booking(
        event_type_id=1,
        start_time_str=start.isoformat(),
        attendee_name=name,
        attendee_email=email,
        attendee_phone=phone,
        title=f"Sales Consultation — {name}",
    )

    if "error" in result:
        log.warning(f"[{call_id}] Cal.com booking failed: {result['error']}")
    else:
        log.info(f"[{call_id}] Cal.com booking created: {result.get('uid')}")
    return result


async def check_and_book(event_type_id: int, date_str: str, time_str: str,
                         name: str, email: str, phone: str) -> tuple[bool, str]:
    """Check slot availability then book. Returns (success, message)."""
    slots = await get_available_slots(event_type_id, date_str)
    if not slots:
        return False, f"No slots available on {date_str}."

    target_iso = f"{date_str}T{time_str}"
    target_dt = datetime.fromisoformat(target_iso)
    target_iso_offset = target_dt.isoformat()

    match = [s for s in slots if s.startswith(target_iso)]
    if not match:
        return False, f"Slot {time_str} on {date_str} is not available."

    result = await create_booking(
        event_type_id=event_type_id,
        start_time_str=target_iso_offset,
        attendee_name=name,
        attendee_email=email,
        attendee_phone=phone,
    )

    if "error" in result:
        return False, f"Booking failed: {result['error']}"
    return True, f"Booked for {date_str} at {time_str}."
