"""Odoo CRM integration via XMLRPC."""
import asyncio
import logging
import re
import xmlrpc.client as xmlrpc
from typing import Optional

log = logging.getLogger(__name__)


class OdooClient:
    """Async wrapper around Odoo XMLRPC API.

    All public methods are async — underlying sync XMLRPC calls
    are offloaded to a thread pool via run_in_executor.
    """

    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self._uid: Optional[int] = None
        self._common: Optional[xmlrpc.ServerProxy] = None
        self._models: Optional[xmlrpc.ServerProxy] = None
        self._connection_failed: bool = False

    # ── internal helpers ─────────────────────────────────────

    def _connect_sync(self) -> Optional[int]:
        if self._connection_failed:
            return None
        try:
            self._common = xmlrpc.ServerProxy(f"{self.url}/xmlrpc/2/common")
            uid = self._common.authenticate(self.db, self.username, self.password, {})
            if not uid:
                raise RuntimeError("Odoo auth failed — check credentials")
            self._models = xmlrpc.ServerProxy(f"{self.url}/xmlrpc/2/object")
            self._uid = uid
            log.info(f"Odoo connected: uid={uid}")
            return uid
        except Exception as e:
            self._connection_failed = True
            log.warning(f"Odoo connection failed (will not retry): {e}")
            return None

    async def connect(self) -> Optional[int]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._connect_sync)

    def _ensure_connected(self):
        if self._uid is None and not self._connection_failed:
            self._connect_sync()

    def _search_sync(self, model: str, domain: list, fields: list) -> list:
        self._ensure_connected()
        ids = self._models.execute_kw(
            self.db, self._uid, self.password, model, "search", [domain]
        )
        if not ids:
            return []
        return self._models.execute_kw(
            self.db, self._uid, self.password, model, "read", [ids], {"fields": fields}
        )

    def _create_sync(self, model: str, values: dict) -> int:
        self._ensure_connected()
        sanitized = {k: self._xml_safe_str(v) if isinstance(v, str) else v for k, v in values.items()}
        return self._models.execute_kw(
            self.db, self._uid, self.password, model, "create", [sanitized]
        )

    def _write_sync(self, model: str, record_id: int, values: dict) -> bool:
        self._ensure_connected()
        sanitized = {k: self._xml_safe_str(v) if isinstance(v, str) else v for k, v in values.items()}
        return self._models.execute_kw(
            self.db, self._uid, self.password, model, "write", [[record_id], sanitized]
        )

    @staticmethod
    def _xml_safe_str(s: str) -> str:
        if not s:
            return s
        return "".join(c for c in s if (
            ord(c) == 0x9 or ord(c) == 0xA or ord(c) == 0xD or
            0x20 <= ord(c) <= 0xD7FF or
            0xE000 <= ord(c) <= 0xFFFD
        ))

    # ── public API ───────────────────────────────────────────

    async def search_partner(self, phone: str) -> dict:
        """Look up a res.partner by phone number.
        Returns first match with id, name, phone, email or empty dict."""
        if not phone:
            return {}
        loop = asyncio.get_event_loop()
        partners = await loop.run_in_executor(
            None, self._search_sync, "res.partner",
            [("phone", "=", phone)],
            ["id", "name", "phone", "email"]
        )
        if not partners:
            return {}
        p = partners[0]
        return {
            "partner_id": p["id"],
            "name": p.get("name") or "",
            "phone": p.get("phone") or "",
            "email": p.get("email") or "",
        }

    async def create_lead(self, lead: dict, conversation: list, call_id: str, agent_name: str) -> Optional[int]:
        """Create a CRM lead in Odoo from call data.
        Returns lead ID or None on failure."""
        loop = asyncio.get_event_loop()
        phone = lead.get("phone") or ""
        partner = await self.search_partner(phone) if phone else {}

        def _xml_safe(s):
            if not s:
                return s
            return "".join(c for c in s if (
                ord(c) == 0x9 or ord(c) == 0xA or ord(c) == 0xD or
                0x20 <= ord(c) <= 0xD7FF or
                0xE000 <= ord(c) <= 0xFFFD
            ))
        transcript = "\n".join(
            f"{'Caller' if m['role'] == 'user' else agent_name}: {_xml_safe(m['content'])}"
            for m in conversation[-20:]  # last 20 turns
        )
        transcript = _xml_safe(transcript)

        values = {
            "name": f"[Call] {lead.get('name') or 'Unknown'}",
            "contact_name": lead.get("name") or "",
            "phone": phone,
            "email_from": lead.get("email") or "",
            "description": (
                f"Call ID: {call_id}\n"
                f"Agent: {agent_name}\n"
                f"Inquiry: {lead.get('inquiry_type') or '-'}\n"
                f"Stall Interest: {lead.get('stall_interest') or '-'}\n"
                f"Complaint: {lead.get('complaint_details') or '-'}\n"
                f"Lead Score: {lead.get('lead_temperature') or '-'}/10\n"
                f"Outcome: {lead.get('outcome') or '-'}\n\n"
                f"Summary:\n{lead.get('summary') or '-'}\n\n"
                f"Transcript (last 20 turns):\n{transcript}"
            ),
        }
        if partner:
            values["partner_id"] = partner["partner_id"]

        try:
            lead_id = await loop.run_in_executor(None, self._create_sync, "crm.lead", values)
            log.info(f"[{call_id}] Odoo lead created: id={lead_id}")
            return lead_id
        except Exception as e:
            log.warning(f"[{call_id}] Odoo lead creation failed: {e}")
            return None

    async def create_helpdesk_ticket(self, lead: dict, conversation: list, call_id: str, agent_name: str) -> Optional[int]:
        """Create a helpdesk.ticket in Odoo from call data.
        Returns ticket ID or None on failure."""
        loop = asyncio.get_event_loop()
        phone = lead.get("phone") or ""
        partner = await self.search_partner(phone) if phone else {}

        def _xml_safe(s):
            if not s:
                return s
            return "".join(c for c in s if (
                ord(c) == 0x9 or ord(c) == 0xA or ord(c) == 0xD or
                0x20 <= ord(c) <= 0xD7FF or
                0xE000 <= ord(c) <= 0xFFFD
            ))
        transcript = "\n".join(
            f"{'Caller' if m['role'] == 'user' else agent_name}: {_xml_safe(m['content'])}"
            for m in conversation[-20:]
        )
        transcript = _xml_safe(transcript)

        values = {
            "name": f"[Call] {lead.get('name') or 'Unknown'} — {lead.get('inquiry_type') or 'General Inquiry'}",
            "partner_name": lead.get("name") or "",
            "partner_email": lead.get("email") or "",
            "description": (
                f"Call ID: {call_id}\n"
                f"Agent: {agent_name}\n"
                f"Phone: {phone}\n"
                f"Inquiry: {lead.get('inquiry_type') or '-'}\n"
                f"Stall Interest: {lead.get('stall_interest') or '-'}\n"
                f"Complaint: {lead.get('complaint_details') or '-'}\n"
                f"Priority Score: {lead.get('lead_temperature') or '-'}/10\n"
                f"Outcome: {lead.get('outcome') or '-'}\n\n"
                f"Summary:\n{lead.get('summary') or '-'}\n\n"
                f"Transcript (last 20 turns):\n{transcript}"
            ),
        }
        if partner:
            values["partner_id"] = partner["partner_id"]

        try:
            ticket_id = await loop.run_in_executor(None, self._create_sync, "helpdesk.ticket", values)
            log.info(f"[{call_id}] Odoo helpdesk ticket created: id={ticket_id}")
            return ticket_id
        except Exception as e:
            log.warning(f"[{call_id}] Odoo helpdesk ticket creation failed: {e}")
            return None

    async def quick_create_ticket(self, call_id: str, agent_name: str, phone: str = "", caller_name: str = "", inquiry: str = "") -> Optional[dict]:
        """Create a minimal helpdesk ticket during a call.
        Returns dict with 'number' and 'id' keys, or None on failure.
        Post-call will update this ticket with full transcript."""
        loop = asyncio.get_event_loop()
        partner = await self.search_partner(phone) if phone else {}
        values = {
            "name": f"[Call] {caller_name or 'Unknown'} — {inquiry or 'General Inquiry'}",
            "partner_name": caller_name or "",
            "description": (
                f"Call ID: {call_id}\n"
                f"Agent: {agent_name}\n"
                f"Phone: {phone}\n"
                f"Created during live call — transcript will be appended post-call."
            ),
        }
        if partner:
            values["partner_id"] = partner["partner_id"]

        try:
            ticket_id = await loop.run_in_executor(None, self._create_sync, "helpdesk.ticket", values)
            # Read back to get ticket number
            ticket_data = await loop.run_in_executor(
                None, self._read_sync, "helpdesk.ticket", ticket_id, ["number", "id"]
            )
            ticket_number = ticket_data.get("number") or str(ticket_id)
            log.info(f"[{call_id}] Quick ticket created: {ticket_number} (id={ticket_id})")
            return {"number": ticket_number, "id": ticket_id}
        except Exception as e:
            log.warning(f"[{call_id}] Quick ticket creation failed: {e}")
            return None

    async def update_helpdesk_ticket(self, ticket_id: int, lead: dict, conversation: list, call_id: str, agent_name: str) -> bool:
        """Update an existing helpdesk ticket with full post-call data.
        Returns True on success."""
        loop = asyncio.get_event_loop()
        phone = lead.get("phone") or ""

        def _xml_safe(s):
            if not s:
                return s
            return "".join(c for c in s if (
                ord(c) == 0x9 or ord(c) == 0xA or ord(c) == 0xD or
                0x20 <= ord(c) <= 0xD7FF or
                0xE000 <= ord(c) <= 0xFFFD
            ))
        transcript = "\n".join(
            f"{'Caller' if m['role'] == 'user' else agent_name}: {_xml_safe(m['content'])}"
            for m in conversation[-20:]
        )
        transcript = _xml_safe(transcript)

        values = {
            "partner_name": lead.get("name") or "",
            "partner_email": lead.get("email") or "",
            "description": (
                f"Call ID: {call_id}\n"
                f"Agent: {agent_name}\n"
                f"Phone: {phone}\n"
                f"Inquiry: {lead.get('inquiry_type') or '-'}\n"
                f"Stall Interest: {lead.get('stall_interest') or '-'}\n"
                f"Complaint: {lead.get('complaint_details') or '-'}\n"
                f"Priority Score: {lead.get('lead_temperature') or '-'}/10\n"
                f"Outcome: {lead.get('outcome') or '-'}\n\n"
                f"Summary:\n{lead.get('summary') or '-'}\n\n"
                f"Transcript (last 20 turns):\n{transcript}"
            ),
        }

        try:
            await loop.run_in_executor(None, self._write_sync, "helpdesk.ticket", ticket_id, values)
            log.info(f"[{call_id}] Odoo helpdesk ticket updated: id={ticket_id}")
            return True
        except Exception as e:
            log.warning(f"[{call_id}] Odoo helpdesk ticket update failed: {e}")
            return False

    def _read_sync(self, model: str, record_id: int, fields: list) -> dict:
        self._ensure_connected()
        result = self._models.execute_kw(
            self.db, self._uid, self.password,
            model, "read",
            [[record_id]], {"fields": fields}
        )
        if result:
            return result[0]
        return {}

    async def search_tickets_by_phone(self, phone: str, partner_name: str = "") -> list:
        """Search open helpdesk tickets by caller phone or name.
        First tries partner_id lookup, then falls back to partner_name search.
        Returns list of dicts with id, name, stage, priority, team, assigned_user, create_date."""
        if not phone and not partner_name:
            return []
        loop = asyncio.get_event_loop()
        partner_id = None
        if phone:
            partner = await self.search_partner(phone)
            partner_id = partner.get("partner_id")

        domain = [("stage_id.closed", "=", False)]
        if partner_id:
            domain.append(("partner_id", "=", partner_id))
        elif partner_name:
            domain.append(("partner_name", "ilike", partner_name))
        else:
            return []

        try:
            tickets = await loop.run_in_executor(
                None, self._search_tickets_sync, domain
            )
            return tickets
        except Exception as e:
            log.warning(f"Ticket search failed: {e}")
            return []

    def _search_tickets_sync(self, domain: list) -> list:
        self._ensure_connected()
        ids = self._models.execute_kw(
            self.db, self._uid, self.password,
            "helpdesk.ticket", "search", [domain], {"order": "create_date desc"}
        )
        if not ids:
            return []
        tickets = self._models.execute_kw(
            self.db, self._uid, self.password,
            "helpdesk.ticket", "read", [ids],
            {
                "fields": [
                    "id", "name", "number", "partner_name",
                    "stage_id", "team_id", "user_id",
                    "priority", "create_date", "closed_date",
                ]
            }
        )
        # Resolve stage/team/user names
        result = []
        for t in tickets:
            stage_name = ""
            if t.get("stage_id") and len(t["stage_id"]) >= 2:
                stage_name = t["stage_id"][1]
            team_name = ""
            if t.get("team_id") and len(t["team_id"]) >= 2:
                team_name = t["team_id"][1]
            assigned = ""
            if t.get("user_id") and len(t["user_id"]) >= 2:
                assigned = t["user_id"][1]
            priority_map = {"0": "Very Low", "1": "Low", "2": "Medium", "3": "High"}
            priority_label = priority_map.get(str(t.get("priority", "")), "Normal")
            result.append({
                "id": t["id"],
                "name": t.get("name", ""),
                "number": t.get("number", ""),
                "stage": stage_name,
                "team": team_name,
                "assigned_to": assigned,
                "priority": priority_label,
                "created": str(t.get("create_date", ""))[:19],
            })
        return result
