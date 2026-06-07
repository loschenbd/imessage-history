"""Unit tests for the redaction component.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the script importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import imessage_export as ie


class ExcelLettersTests(unittest.TestCase):
    def test_zero_is_a(self):
        self.assertEqual(ie._excel_letters(0), "A")

    def test_twenty_five_is_z(self):
        self.assertEqual(ie._excel_letters(25), "Z")

    def test_twenty_six_is_aa(self):
        self.assertEqual(ie._excel_letters(26), "AA")

    def test_twenty_seven_is_ab(self):
        self.assertEqual(ie._excel_letters(27), "AB")

    def test_701_is_zz(self):
        self.assertEqual(ie._excel_letters(701), "ZZ")


class RedactionConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = ie.RedactionConfig(me_name="Ben")
        self.assertEqual(cfg.me_name, "Ben")
        self.assertEqual(cfg.extra_names, [])
        self.assertTrue(cfg.redact_phones)
        self.assertTrue(cfg.redact_emails)
        self.assertTrue(cfg.redact_urls)
        self.assertFalse(cfg.case_sensitive)

    def test_disable_phones(self):
        cfg = ie.RedactionConfig(me_name="Ben", redact_phones=False)
        self.assertFalse(cfg.redact_phones)


def _msg(message_id, is_from_me, author_label, timestamp="2025-05-01 14:00:00",
         sender_handle=None, text="", chat_id=1):
    """Build a Message with sensible defaults for redactor tests."""
    return ie.Message(
        message_id=message_id,
        timestamp=timestamp,
        timestamp_utc=timestamp.replace(" ", "T") + "+00:00",
        chat_id=chat_id,
        sender_handle=sender_handle,
        is_from_me=is_from_me,
        author_label=author_label,
        text=text,
        has_attachment=0,
        attachment_filenames=[],
    )


def _metadata(participants):
    return {
        "me_name": "Ben",
        "participants": [
            {"handle": h, "service": "iMessage", "resolved_name": name}
            for h, name in participants
        ],
        "window": {"local_start": "", "local_end": "", "utc_start": "", "utc_end": "",
                   "tz": "UTC", "apple_ns_start": None, "apple_ns_end": None,
                   "input": {}},
        "actual_first_local": "2025-05-01 14:00:00",
        "actual_last_local":  "2025-05-01 14:05:00",
        "chats": [{"display_name": "", "style": 45, "chat_identifier": "+15551234567",
                   "is_group": False}],
        "message_count": 1,
        "chat_ids": [1],
        "exported_at": "2025-05-01T14:05:00+00:00",
        "timestamp_unit_detected": "ns",
        "attribution_note": "",
    }


class PseudonymMapTests(unittest.TestCase):
    def test_me_is_always_person_a_even_if_speaks_second(self):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567"),
            _msg(2, is_from_me=1, author_label="Ben",   sender_handle=None),
        ]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()
        self.assertEqual(pmap["aliases_to_pseudonym"]["Ben"], "Person A")
        self.assertEqual(pmap["aliases_to_pseudonym"]["Alice"], "Person B")

    def test_timeline_ordered_b_then_c(self):
        # Alice speaks first, then Bob — group chat
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567"),
            _msg(2, is_from_me=0, author_label="Bob",   sender_handle="+15557654321"),
            _msg(3, is_from_me=1, author_label="Ben",   sender_handle=None),
        ]
        md = _metadata([("+15551234567", "Alice"), ("+15557654321", "Bob")])
        r = ie.Redactor(messages, md, contacts={
                            "+15551234567": "Alice", "+15557654321": "Bob"},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()["aliases_to_pseudonym"]
        self.assertEqual(pmap["Ben"],   "Person A")
        self.assertEqual(pmap["Alice"], "Person B")
        self.assertEqual(pmap["Bob"],   "Person C")

    def test_handles_share_pseudonym_with_name(self):
        # The handle and the contact name both map to the same Person
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()["aliases_to_pseudonym"]
        self.assertEqual(pmap["Alice"],         pmap["+15551234567"])

    def test_extra_names_get_their_own_pseudonym(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben", extra_names=["Carol"]))
        pmap = r.pseudonym_map()["aliases_to_pseudonym"]
        self.assertEqual(pmap["Carol"], "Person C")  # after Ben(A) and Alice(B)

    def test_grouped_view_present(self):
        messages = [_msg(1, is_from_me=1, author_label="Ben")]
        md = _metadata([])
        r = ie.Redactor(messages, md, contacts={},
                        config=ie.RedactionConfig(me_name="Ben"))
        pmap = r.pseudonym_map()
        self.assertIn("people", pmap)
        self.assertIn("aliases_to_pseudonym", pmap)
        person_a = next(p for p in pmap["people"] if p["pseudonym"] == "Person A")
        self.assertIn("Ben", person_a["aliases"])

    def test_empty_me_name_raises(self):
        with self.assertRaises(ValueError):
            ie.Redactor(messages=[], metadata=_metadata([]), contacts={},
                        config=ie.RedactionConfig(me_name=""))

    def test_people_sorted_naturally_past_z(self):
        # Build a redactor with 28 distinct names so we get Person A through Person AB
        messages = []
        contacts = {}
        for i in range(27):  # Ben (A) + 27 incoming = 28 total
            handle = f"+1555{i:07d}"
            name = f"Speaker{i:02d}"
            messages.append(_msg(i + 1, is_from_me=0, author_label=name, sender_handle=handle))
            contacts[handle] = name
        md = _metadata([(h, n) for h, n in contacts.items()])
        r = ie.Redactor(messages, md, contacts=contacts,
                        config=ie.RedactionConfig(me_name="Ben"))
        people = r.pseudonym_map()["people"]
        # Should be ordered A, B, C, ..., Z, AA, AB (natural — not lexicographic)
        pseudonyms = [p["pseudonym"] for p in people]
        self.assertEqual(pseudonyms[0], "Person A")
        self.assertEqual(pseudonyms[25], "Person Z")
        self.assertEqual(pseudonyms[26], "Person AA")
        self.assertEqual(pseudonyms[27], "Person AB")


class RedactTextTests(unittest.TestCase):
    def _make(self, **cfg):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567"),
            _msg(2, is_from_me=1, author_label="Ben"),
        ]
        md = _metadata([("+15551234567", "Alice")])
        return ie.Redactor(messages, md,
                           contacts={"+15551234567": "Alice"},
                           config=ie.RedactionConfig(me_name="Ben", **cfg))

    def test_alias_substituted_in_text(self):
        r = self._make()
        self.assertEqual(r._redact_text("Alice said hi"), "Person B said hi")

    def test_case_insensitive_by_default(self):
        r = self._make()
        self.assertEqual(r._redact_text("alice said hi"), "Person B said hi")

    def test_case_sensitive_mode_respected(self):
        r = self._make(case_sensitive=True)
        self.assertEqual(r._redact_text("alice said hi"), "alice said hi")
        self.assertEqual(r._redact_text("Alice said hi"), "Person B said hi")

    def test_longest_alias_wins(self):
        # If both "Alice" and "Alice Smith" map to same person, the longer match should
        # win (no leftover " Smith" suffix).
        messages = [_msg(1, is_from_me=0, author_label="Alice")]
        md = _metadata([])
        r = ie.Redactor(messages, md,
                        contacts={"+15551234567": "Alice Smith", "+15557654321": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        # Both should map to the same Person B (since the names key together via
        # _build_pseudonym_map only if they share a handle — verify behavior is at
        # least that the longer name fully consumes).
        self.assertNotIn("Smith", r._redact_text("Alice Smith said hi"))

    def test_phone_redacted(self):
        r = self._make()
        self.assertEqual(r._redact_text("call me at +15557654321"), "call me at [PHONE]")

    def test_phone_redaction_can_be_disabled(self):
        r = self._make(redact_phones=False)
        self.assertEqual(r._redact_text("call me at +15557654321"), "call me at +15557654321")

    def test_email_redacted(self):
        r = self._make()
        self.assertEqual(r._redact_text("write alice@example.com"), "write [EMAIL]")

    def test_url_redacted(self):
        r = self._make()
        self.assertEqual(r._redact_text("see https://example.com/x"), "see [URL]")

    def test_regex_metacharacters_in_alias_safe(self):
        # "O'Brien" and "(work)" as contact names must not crash or match unintended substrings.
        messages = [_msg(1, is_from_me=0, author_label="O'Brien")]
        md = _metadata([])
        r = ie.Redactor(messages, md,
                        contacts={"+15551234567": "O'Brien", "+15557654321": "(work)"},
                        config=ie.RedactionConfig(me_name="Ben"))
        out = r._redact_text("Met O'Brien at (work) yesterday")
        self.assertNotIn("O'Brien", out)
        self.assertNotIn("(work)",  out)

    def test_phone_does_not_eat_following_word(self):
        r = self._make()
        # Order numbers / confirmation codes may get matched as phones (false positive,
        # acceptable). But the match must not consume the space that follows.
        out = r._redact_text("order #12345678 confirmed")
        self.assertIn(" confirmed", out)
        self.assertNotIn("[PHONE]confirmed", out)

    def test_url_does_not_eat_trailing_period(self):
        r = self._make()
        out = r._redact_text("see https://example.com.")
        self.assertEqual(out, "see [URL].")

    def test_url_does_not_eat_trailing_comma(self):
        r = self._make()
        out = r._redact_text("see https://example.com, then reply")
        self.assertEqual(out, "see [URL], then reply")

    def test_url_does_not_eat_trailing_paren(self):
        r = self._make()
        out = r._redact_text("link (https://example.com)")
        self.assertEqual(out, "link ([URL])")


class RedactMessagesTests(unittest.TestCase):
    def _make(self):
        messages = [
            _msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567",
                 text="Hey Ben"),
            _msg(2, is_from_me=1, author_label="Ben",   sender_handle=None,
                 text="Hi Alice — email me at b@x.com"),
        ]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        return r, messages, md

    def test_author_label_pseudonymized(self):
        r, _, _ = self._make()
        redacted = r.redact_messages()
        self.assertEqual(redacted[0].author_label, "Person B")
        self.assertEqual(redacted[1].author_label, "Person A")

    def test_text_pseudonymized(self):
        r, _, _ = self._make()
        redacted = r.redact_messages()
        self.assertEqual(redacted[0].text, "Hey Person A")
        self.assertIn("Person B", redacted[1].text)
        self.assertIn("[EMAIL]",  redacted[1].text)

    def test_original_messages_untouched(self):
        r, originals, _ = self._make()
        _ = r.redact_messages()
        self.assertEqual(originals[0].author_label, "Alice")
        self.assertEqual(originals[0].text,         "Hey Ben")

    def test_sender_handle_replaced_when_present(self):
        r, _, _ = self._make()
        redacted = r.redact_messages()
        self.assertEqual(redacted[0].sender_handle, "Person B")
        self.assertIsNone(redacted[1].sender_handle)


class RedactMetadataTests(unittest.TestCase):
    def test_participants_pseudonymized(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        red_md = r.redact_metadata()
        p = red_md["participants"][0]
        self.assertEqual(p["resolved_name"], "Person B")
        self.assertEqual(p["handle"],        "Person B")

    def test_original_metadata_untouched(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        _ = r.redact_metadata()
        self.assertEqual(md["participants"][0]["resolved_name"], "Alice")


class ChatLabelTests(unittest.TestCase):
    def test_one_on_one_uses_other_party_pseudonym(self):
        messages = [_msg(1, is_from_me=0, author_label="Alice", sender_handle="+15551234567")]
        md = _metadata([("+15551234567", "Alice")])
        r = ie.Redactor(messages, md, contacts={"+15551234567": "Alice"},
                        config=ie.RedactionConfig(me_name="Ben"))
        self.assertEqual(r.chat_label(), "Person B")


class CliArgsTests(unittest.TestCase):
    def test_flags_parse(self):
        parser = ie.build_parser()
        args = parser.parse_args([
            "--chat-id", "1",
            "--redact",
            "--redact-names-file", "/tmp/x.txt",
            "--no-redact-phones",
        ])
        self.assertTrue(args.redact)
        self.assertFalse(args.redact_only)
        self.assertEqual(args.redact_names_file, "/tmp/x.txt")
        self.assertTrue(args.no_redact_phones)

    def test_suggest_names_with_redact_is_rejected(self):
        with self.assertRaises(SystemExit):
            ie.main([
                "--db", "/tmp/non-existent.db",
                "--chat-id", "1",
                "--suggest-names",
                "--redact",
            ])


if __name__ == "__main__":
    unittest.main()
