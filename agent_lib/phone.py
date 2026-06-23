"""Phone number utilities."""
import re


def normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    p = re.sub(r'\D', '', str(phone))
    if len(p) < 7:
        return ""
    if p.startswith("0") and len(p) == 11:
        return "+92" + p[1:]
    if p.startswith("92") and len(p) == 12:
        return "+" + p
    if p.startswith("+"):
        return phone
    return "+" + p
