"""Run: python -m unittest discover -s eval -p test_calendar_acceptance_filter.py -v"""

import asyncio
import json
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calendar_acceptance_filter import is_calendar_acceptance_notification


# Anonymized reproduction of the reported notification. No live credentials,
# personal addresses, meeting access codes, databases, or model calls are needed.
BODY = '''Example Applicant has accepted this invitation.
Join with Google Meet
Meeting link
meet.google.com/aaa-bbbb-ccc
Join by phone
(US) +1 555-0100
PIN: 123456
More phone numbers
When
Weekly from 6pm to 7pm on Wednesday (Eastern Time - New York)
Guests
organizer@example.org - organizer
Example Applicant
View all guest info
Invitation from Google Calendar
You are receiving this email because you are subscribed to calendar notifications. To stop receiving these emails, go to Calendar settings, select this calendar, and change "Other notifications".
Forwarding this invitation could allow any recipient to send a response to the organizer, be added to the guest list, invite others regardless of their own invitation status, or modify your RSVP. Learn more'''
SESSION = "CDF Onboarding: Portfolio Session"


class CalendarTests(unittest.TestCase):
    def test_both_patterns_capitalization_and_prefixes(self):
        for subject in ("Accepted: Other meeting", SESSION, "Accepted: " + SESSION):
            for case in (subject, subject.upper(), subject.lower()):
                for prefix in ("", "Re: ", "Fwd: ", "RE: Fw: Re: "):
                    with self.subTest(subject=prefix + case):
                        self.assertTrue(is_calendar_acceptance_notification(prefix + case, BODY))

    def test_html_notification(self):
        html = "<html><body>" + "".join("<div>" + line + "</div>" for line in BODY.splitlines()) + "</body></html>"
        self.assertTrue(is_calendar_acceptance_notification("Accepted: " + SESSION, html))

    def test_legitimate_emails_are_not_skipped(self):
        for subject in (SESSION, "Accepted: " + SESSION, "Re: Accepted: " + SESSION,
                        "Question about " + SESSION, "My application was accepted: next steps"):
            for body in ("When is the session?", "Please send the recording.",
                         "I need help joining.\n\n" + BODY,
                         BODY + "\nCan you help me?",
                         "<p>Please resend the link.</p><blockquote>" + BODY + "</blockquote>",
                         "", "I have accepted this invitation."):
                with self.subTest(subject=subject, body=body[:40]):
                    self.assertFalse(is_calendar_acceptance_notification(subject, body))

    def test_unrelated_subject_is_not_skipped(self):
        self.assertFalse(is_calendar_acceptance_notification("Payment question", BODY))

    def test_api_skips_before_templates_or_drafting(self):
        # Import without loading real .env values; do not run app startup/lifespan.
        root = Path(__file__).resolve().parents[1]
        # The replica has no static assets yet. Disable only the directory check
        # during import; no startup, database, or external integration is run.
        from fastapi.staticfiles import StaticFiles
        spec = importlib.util.spec_from_file_location(
            "fellowship_test_app", root / "__init__.py",
            submodule_search_locations=[str(root)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        with patch("dotenv.load_dotenv"), patch("fastapi.staticfiles.StaticFiles",
                side_effect=lambda **kwargs: StaticFiles(**kwargs, check_dir=False)):
            from fellowship_test_app import main
        for subject in ("Re: ACCEPTED: " + SESSION, SESSION.lower()):
            request = SimpleNamespace(headers={"content-type": "application/json"},
                json=AsyncMock(return_value={"subject": subject, "body": BODY,
                    "from": "applicant@example.org", "mailbox": "hr@example.org"}))
            with patch.object(main, "_require_api_key"), patch.object(main.db, "_utc_now", return_value="test"), \
                 patch.object(main.db, "get_auto_reply_templates") as templates, \
                 patch.object(main, "process_email") as process, \
                 patch.object(main, "_notify_make_case_approved") as send:
                response = asyncio.run(main.api_process_email(request))
                self.assertEqual(json.loads(response.body)["skip_reason"], "automated_subject")
                templates.assert_not_called()
                process.assert_not_called()
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
