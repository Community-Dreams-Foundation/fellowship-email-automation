"""Conservative recognition of Calendar acceptance notifications, without AI."""

import re
from html import unescape
from html.parser import HTMLParser


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "tr", "td", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "tr", "td", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def is_calendar_acceptance_notification(subject: str, body: str) -> bool:
    """Skip only a recognizable notification, not every email with its subject.

    Unrecognized formats continue through processing. A reply/forward containing
    new text before the notification is deliberately not treated as automated.
    """
    normalized_subject = re.sub(
        r"^(?:(?:re|fw|fwd)\s*:\s*)+", "", subject.strip(), flags=re.I
    )
    if not (
        re.match(r"accepted\s*:", normalized_subject, flags=re.I)
        or re.match(r"cdf onboarding:\s*portfolio session\b", normalized_subject, flags=re.I)
    ):
        return False

    parser = _VisibleText()
    parser.feed(body)
    text = unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines or not re.fullmatch(r"[^\n]+ has accepted this invitation\.", lines[0], re.I):
        return False

    # The structure and footer provide evidence beyond the subject or a quoted
    # acceptance phrase. Do not discard arbitrary text surrounding this structure.
    pattern = (
        r"[^\n]+ has accepted this invitation\.\n"
        r"(?:Join with Google Meet\n)?"
        r"Meeting link\n(?:https?://)?meet\.google\.com/[a-z-]+(?:\?[^\s]*)?\n"
        r"(?:Join by phone\n[^\n]+\nPIN: [\d #]+\n(?:More phone numbers\n)?)?"
        r"When\n[^\n]+\nGuests\n"
        r"(?:[^\n]+\n){1,100}?"
        r"(?:View all guest info\n)?Invitation from Google Calendar\n"
        r"You are receiving this email because you are subscribed to calendar notifications\. "
        r"To stop receiving these emails, go to Calendar settings, select this calendar, "
        r'and change ["\u201c]Other notifications["\u201d]\.('
        r"\nForwarding this invitation could allow any recipient to send a response to the organizer, "
        r"be added to the guest list, invite others regardless of their own invitation status, "
        r"or modify your RSVP\. Learn more)?"
    )
    return re.fullmatch(pattern, "\n".join(lines), re.I) is not None
