"""Opt-in redaction / pseudonymization.

`RedactionConfig` configures one redaction pass. `Redactor` builds a
deterministic alias→pseudonym map for one conversation (device owner is
always "Person A"; the second new speaker is "Person B"; …) and then
exposes `redact_messages()`, `redact_metadata()`, and `pseudonym_map()`.

`suggest_names()` is a separate helper that scans message bodies for
proper-noun candidates not already in the contacts CSV — used to
bootstrap a `--redact-names-file`.

Deviation from the plan: this module did not appear in the original
restructure plan; the redactor was originally going to live in
`writers.py`. It was promoted to its own module because the public
surface (Redactor + RedactionConfig + suggest_names + helpers) was
large enough to muddle writers.py's responsibility.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from .db import chat_label
from .models import Message


@dataclass(frozen=True)
class RedactionConfig:
    me_name: str
    extra_names: list[str] = field(default_factory=list)
    redact_phones: bool = True
    redact_emails: bool = True
    redact_urls: bool = True
    case_sensitive: bool = False


def _excel_letters(n: int) -> str:
    """Spreadsheet-column-style letters: 0→A, 25→Z, 26→AA, 27→AB, …, 701→ZZ."""
    if n < 0:
        raise ValueError("_excel_letters requires n >= 0")
    s = ""
    n += 1  # shift to 1-indexed so the math works cleanly
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


class Redactor:
    """Build a deterministic alias→pseudonym map for one conversation.

    Inputs:
      messages : list[Message]    (timeline order, as produced by export())
      metadata : dict             (the metadata dict produced by export())
      contacts : dict[str, str]   (handle → name, as loaded from contacts.csv)
      config   : RedactionConfig

    The map is built once at __init__ time and re-used by the redact_* methods.
    """

    def __init__(self, messages, metadata, contacts, config):
        if not config.me_name:
            raise ValueError("RedactionConfig.me_name must be non-empty")
        self._messages  = messages
        self._metadata  = metadata
        self._contacts  = contacts or {}
        self._config    = config
        self._alias_to_pseudonym: dict[str, str] = {}
        self._pseudonym_to_aliases: dict[str, list[str]] = {}
        self._build_pseudonym_map()

    def _assign_pseudonym(self, alias: str, pseudonym: str) -> None:
        """Map alias → pseudonym. No-op if alias already mapped."""
        if not alias or alias in self._alias_to_pseudonym:
            return
        self._alias_to_pseudonym[alias] = pseudonym
        self._pseudonym_to_aliases.setdefault(pseudonym, []).append(alias)

    def _new_pseudonym(self) -> str:
        n = len(self._pseudonym_to_aliases)
        return f"Person {_excel_letters(n)}"

    def _ensure_person(self, primary_alias: str, *aliases: str) -> str:
        """Get (or create) the pseudonym for primary_alias, registering aliases under it."""
        existing = self._alias_to_pseudonym.get(primary_alias)
        if existing is None:
            existing = self._new_pseudonym()
        self._assign_pseudonym(primary_alias, existing)
        for a in aliases:
            self._assign_pseudonym(a, existing)
        return existing

    def _build_pseudonym_map(self) -> None:
        # 1. Device owner is always Person A.
        self._ensure_person(self._config.me_name)

        # 2. Walk the message timeline assigning new speakers.
        for m in self._messages:
            if m.is_from_me:
                # Outgoing — author is me; nothing new to register.
                continue
            label  = m.author_label
            handle = m.sender_handle
            # Both label and handle (when present) belong to the same person.
            if label:
                self._ensure_person(label, *([handle] if handle else []))
            elif handle:
                self._ensure_person(handle)

        # 3. Register all contact names (even ones not in this conversation —
        #    they may be mentioned in body text from third-party speakers).
        for handle, name in self._contacts.items():
            if name:
                self._ensure_person(name, handle)

        # 4. Register --redact-names-file extras.
        for extra in self._config.extra_names:
            if extra:
                self._ensure_person(extra)

    def pseudonym_map(self) -> dict:
        def _sort_key(item):
            pseudonym = item[0]
            letters = pseudonym.removeprefix("Person ")
            return (len(letters), letters)
        people = [
            {"pseudonym": p, "aliases": list(aliases)}
            for p, aliases in sorted(self._pseudonym_to_aliases.items(), key=_sort_key)
        ]
        return {
            "aliases_to_pseudonym": dict(self._alias_to_pseudonym),
            "people": people,
        }

    # PII regexes. Conservative; documented as best-effort in README.
    # Phone uses a negative lookbehind for word chars so a leading "+" at the
    # start of a token (e.g. "+15551234567" after a space) matches cleanly —
    # \b doesn't sit between a non-word space and the non-word "+".
    _PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\-().]{6,}\d(?!\w)")
    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _URL_RE   = re.compile(r"https?://[^\s<>\"'`]+?(?=[.,;!?)\]\}>]*(?:\s|$))")

    def _ordered_aliases(self) -> list[str]:
        """Aliases ordered longest-first so 'Alice Smith' wins over 'Alice'."""
        return sorted(self._alias_to_pseudonym.keys(), key=len, reverse=True)

    def _redact_text(self, s: str) -> str:
        if not s:
            return s
        out = s
        # Scrub PII first so an alias inside an email local-part
        # ("alice@example.com") doesn't get partially substituted before the
        # email regex can match the whole address.
        if self._config.redact_phones:
            out = self._PHONE_RE.sub("[PHONE]", out)
        if self._config.redact_emails:
            out = self._EMAIL_RE.sub("[EMAIL]", out)
        if self._config.redact_urls:
            out = self._URL_RE.sub("[URL]", out)
        case_sensitive = self._config.case_sensitive
        for alias in self._ordered_aliases():
            pseudonym = self._alias_to_pseudonym[alias]
            # `(?<!\w)alias(?!\w)` matches the alias only when not adjacent to
            # word characters. Prevents "Ben" from matching inside "Bend",
            # "Alice" inside "Alicethe", etc. `re.escape` neutralizes any
            # regex metacharacters in user-supplied contact names.
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
            out = re.sub(pattern, pseudonym, out, flags=flags)
        return out

    def redact_messages(self) -> list[Message]:
        out = []
        for m in self._messages:
            new = copy.deepcopy(m)
            if new.author_label in self._alias_to_pseudonym:
                new.author_label = self._alias_to_pseudonym[new.author_label]
            if new.sender_handle and new.sender_handle in self._alias_to_pseudonym:
                new.sender_handle = self._alias_to_pseudonym[new.sender_handle]
            new.text = self._redact_text(new.text)
            if new.reaction:
                rdict = dict(new.reaction)
                if rdict.get("target_text"):
                    rdict["target_text"] = self._redact_text(rdict["target_text"])
                if rdict.get("target_author") in self._alias_to_pseudonym:
                    rdict["target_author"] = self._alias_to_pseudonym[rdict["target_author"]]
                new.reaction = rdict
            out.append(new)
        return out

    def redact_metadata(self) -> dict:
        out = copy.deepcopy(self._metadata)
        for p in out.get("participants", []):
            for key in ("handle", "resolved_name"):
                v = p.get(key)
                if v and v in self._alias_to_pseudonym:
                    p[key] = self._alias_to_pseudonym[v]
        # Chat headers carry the raw chat_identifier (a phone/email for 1:1s)
        # and an optional display_name (a free-text group name). Run both
        # through _redact_text so participant handles + PII regexes scrub
        # them — otherwise the redacted JSON metadata leaks the real handle.
        for c in out.get("chats", []):
            for key in ("chat_identifier", "display_name"):
                v = c.get(key)
                if v:
                    c[key] = self._redact_text(v)
        # me_name in metadata stays as the original label so the AI-ready header
        # accurately describes who "Person A" is in the redacted view.
        if out.get("me_name") in self._alias_to_pseudonym:
            out["me_name"] = self._alias_to_pseudonym[out["me_name"]]
        return out

    def chat_label(self) -> str:
        # 1:1 → the other participant's pseudonym.
        # Group → fall back to the existing chat_label() applied to redacted metadata.
        red_md = self.redact_metadata()
        return chat_label(red_md)


# Token patterns + stopwords for --suggest-names.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b")
_SUGGEST_STOPWORDS = {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
    "The", "This", "That", "What", "Who", "Why", "How", "When", "Where",
    "I", "My", "Me", "We", "Our", "Us", "He", "She", "It", "They", "Them",
    "But", "And", "So", "Or", "If", "Yes", "No", "OK", "Okay", "Just",
    "Hi", "Hey", "Hello", "Thanks", "Thank", "Sorry",
}


def suggest_names(messages: list[Message], contacts: dict[str, str]) -> int:
    """Print proper-noun candidates not already in `contacts`.

    Output format: comment-prefixed lines for context, one candidate per line.
    User redirects to a file, deletes false positives, passes via
    --redact-names-file.
    """
    known = {name.lower() for name in contacts.values() if name}
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}

    for m in messages:
        if not m.text:
            continue
        for match in _PROPER_NOUN_RE.finditer(m.text):
            tok = match.group(0)
            if tok in _SUGGEST_STOPWORDS:
                continue
            if tok.lower() in known:
                continue
            counts[tok] = counts.get(tok, 0) + 1
            if tok not in samples:
                start = max(0, match.start() - 60)
                end   = min(len(m.text), match.end() + 60)
                samples[tok] = m.text[start:end].replace("\n", " ").strip()

    # Drop singletons.
    counts = {k: v for k, v in counts.items() if v >= 2}

    print("# Proper-noun candidates not in contacts.csv.")
    print("# Review and remove false positives, then pass via --redact-names-file.")
    print("")
    for tok in sorted(counts, key=lambda t: (-counts[t], t)):
        print(f"# {counts[tok]}× — {samples[tok]!r}")
        print(tok)
    return 0
