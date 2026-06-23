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
        return self._models.execute_kw(
            self.db, self._uid, self.password, model, "create", [values]
        )

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

        _xml_safe = lambda s: re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s) if s else s
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
