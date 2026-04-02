import re

BLOCK_PATTERNS = [
    re.compile(r"\b\+?\d[\d\s\-]{7,}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"\b(whatsapp|telegram|gmail|phone|call me|dm me|contact me|instagram|imo|number)\b", re.I),
]


def message_contains_blocked_contact_info(message: str) -> tuple[bool, str]:
    if not message:
        return False, ""

    text = message.strip()

    for pattern in BLOCK_PATTERNS:
        if pattern.search(text):
            return True, "Sharing personal contact details is not allowed in chat."

    return False, ""