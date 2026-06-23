"""Chatwoot CRM integration."""
import logging
import httpx

from .phone import normalize_phone

log = logging.getLogger(__name__)


async def chatwoot_lookup(phone: str, cfg) -> dict:
    if not cfg.chatwoot_url or not cfg.chatwoot_token or not phone:
        return {}
    try:
        headers = {"api_access_token": cfg.chatwoot_token}
        base = f"{cfg.chatwoot_url}/api/v1/accounts/{cfg.chatwoot_account}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{base}/contacts/search", headers=headers, params={"q": phone})
            contacts = r.json().get("payload", [])
            if not contacts:
                return {}
            contact = contacts[0]
            contact_id = contact["id"]
            name = contact.get("name") or ""
            r = await client.get(f"{base}/contacts/{contact_id}/notes", headers=headers)
            notes = r.json().get("payload", [])
            notes = sorted(notes, key=lambda x: x.get('created_at', 0), reverse=True)
            history_parts = []
            for note in notes[:3]:
                content = note.get("content", "").strip()
                if content:
                    history_parts.append(content)
            return {
                "contact_id": contact_id,
                "name": name,
                "total_calls": len(notes),
                "history": "\n\n---\n\n".join(history_parts),
            }
    except Exception as e:
        log.warning(f"Chatwoot lookup failed: {e}")
        return {}


async def create_chatwoot_lead(lead: dict, conversation: list, call_id: str, agent_name: str, cfg) -> None:
    if not cfg.chatwoot_url or not cfg.chatwoot_token:
        return
    headers = {"api_access_token": cfg.chatwoot_token, "Content-Type": "application/json"}
    base = f"{cfg.chatwoot_url}/api/v1/accounts/{cfg.chatwoot_account}"
    custom_attrs = {
        "inquiry_type":      lead.get('inquiry_type'),
        "stall_interest":    lead.get('stall_interest'),
        "complaint_details": lead.get('complaint_details'),
        "lead_temperature":  lead.get('lead_temperature'),
        "outcome":           lead.get('outcome'),
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            contact_id = None
            e164_phone = normalize_phone(lead.get('phone') or '')

            if e164_phone:
                r = await client.get(f"{base}/contacts/search", headers=headers, params={"q": e164_phone})
                existing = r.json().get('payload', [])
                if existing:
                    contact_id = existing[0]['id']
                    patch_name = lead.get('name') or existing[0].get('name') or 'Unknown Caller'
                    await client.patch(f"{base}/contacts/{contact_id}", headers=headers,
                                       json={"name": patch_name, "custom_attributes": custom_attrs})
            if not contact_id:
                r = await client.post(f"{base}/contacts", headers=headers, json={
                    "name": lead.get('name') or 'Unknown Caller',
                    "phone_number": e164_phone or None,
                    "email": lead.get('email'),
                    "custom_attributes": custom_attrs,
                })
                rdata = r.json()
                contact_id = rdata.get('payload', {}).get('contact', {}).get('id')
                if not contact_id:
                    log.warning(f"[{call_id}] Chatwoot: contact creation failed")
                    return

            r = await client.post(f"{base}/conversations", headers=headers, json={
                "contact_id": contact_id,
                "inbox_id": cfg.chatwoot_inbox,
            })
            r.raise_for_status()
            conv_id = r.json().get('id')
            if not conv_id:
                log.warning(f"[{call_id}] Chatwoot: conversation creation returned no id")
                return

            transcript = "\n".join([
                f"{'Caller' if m['role'] == 'user' else agent_name}: {m['content']}"
                for m in conversation
            ])
            await client.post(f"{base}/conversations/{conv_id}/messages", headers=headers,
                              json={"content": f"TRANSCRIPT\n\n{transcript}",
                                    "message_type": "outgoing", "private": True})

            stall = lead.get('stall_interest')
            note_content = (
                f"CALL SUMMARY - {call_id}\n\n"
                f"{lead.get('summary') or ''}\n\n"
                f"Inquiry:     {lead.get('inquiry_type') or '-'}\n"
                f"Stall:       {'Yes' if stall is True else 'No' if stall is False else '-'}\n"
                f"Complaint:   {lead.get('complaint_details') or '-'}\n"
                f"Score:       {lead.get('lead_temperature') or '-'}/10\n"
                f"Outcome:     {lead.get('outcome') or '-'}"
            )
            await client.post(f"{base}/contacts/{contact_id}/notes", headers=headers,
                              json={"content": note_content})

            log.info(f"[{call_id}] Chatwoot: done — contact {contact_id}, conv {conv_id}")
    except Exception as e:
        log.warning(f"[{call_id}] Chatwoot failed: {e}")
