"""Gmail notification via SMTP."""
import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

log = logging.getLogger(__name__)


async def send_gmail_notification(lead: dict, conversation: list, call_id: str, agent_name: str, cfg) -> None:
    if not cfg.gmail_sender or not cfg.gmail_password or not cfg.gmail_to:
        return
    try:
        name = lead.get('name') or 'Unknown'
        inq = lead.get('inquiry_type') or 'Not specified'
        temp = lead.get('lead_temperature', '?')
        stall = lead.get('stall_interest')
        stall_str = 'Yes' if stall is True else 'No' if stall is False else '—'
        transcript_text = "\n".join([
            f"{'Caller' if m['role'] == 'user' else agent_name}: {m['content']}"
            for m in conversation
        ])
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"New Lead - {name} - {inq} - Score {temp}/10 - {agent_name}"
        msg["From"] = cfg.gmail_sender
        msg["To"] = cfg.gmail_to
        score_color = "green" if isinstance(temp, int) and temp >= 7 else "orange"
        html = f"""<html><body style="font-family:Arial,sans-serif;max-width:600px">
<h2 style="color:#2c3e50">New Lead - {agent_name}</h2>
<table style="border-collapse:collapse;width:100%">
<tr><td style="padding:8px;border:1px solid #ddd"><b>Name</b></td><td style="padding:8px;border:1px solid #ddd">{lead.get('name') or '—'}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd"><b>Phone</b></td><td style="padding:8px;border:1px solid #ddd">{lead.get('phone') or '—'}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd"><b>Email</b></td><td style="padding:8px;border:1px solid #ddd">{lead.get('email') or '—'}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd"><b>Inquiry Type</b></td><td style="padding:8px;border:1px solid #ddd">{inq}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd"><b>Stall Interest</b></td><td style="padding:8px;border:1px solid #ddd">{stall_str}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd"><b>Complaint</b></td><td style="padding:8px;border:1px solid #ddd">{lead.get('complaint_details') or '—'}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd"><b>Lead Score</b></td><td style="padding:8px;border:1px solid #ddd"><b style="color:{score_color}">{temp}/10</b></td></tr>
<tr><td style="padding:8px;border:1px solid #ddd"><b>Outcome</b></td><td style="padding:8px;border:1px solid #ddd">{lead.get('outcome') or '—'}</td></tr>
</table>
<h3 style="color:#2c3e50;margin-top:20px">Summary</h3>
<p style="background:#f8f9fa;padding:12px;border-radius:4px">{lead.get('summary') or '—'}</p>
<h3 style="color:#2c3e50">Transcript</h3>
<pre style="background:#f8f9fa;padding:12px;border-radius:4px;white-space:pre-wrap;font-size:13px">{transcript_text}</pre>
<hr/><p style="color:#7f8c8d;font-size:12px">PSBA AI — {agent_name} — Call ID: {call_id}</p>
</body></html>"""
        msg.attach(MIMEText(html, "html"))
        loop = asyncio.get_event_loop()
        def _send():
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(cfg.gmail_sender, cfg.gmail_password)
                s.sendmail(cfg.gmail_sender, cfg.gmail_to, msg.as_string())
        await loop.run_in_executor(None, _send)
        log.info(f"[{call_id}] Gmail sent to {cfg.gmail_to}")
    except Exception as e:
        log.warning(f"[{call_id}] Gmail failed: {e}")
