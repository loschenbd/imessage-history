"""Pure rendering helpers for HistoryView.

This module owns every format / cache / style decision that doesn't
need access to a Textual app or to mutate a widget. The four units
are:

  * `classify` + `_render_body` — taxonomy and per-kind body string.
  * `format_run` — one speaker run in, segments + byte ranges out.
  * `_ChunkRender` — one mounted chunk's cached state (unstyled blob,
    per-msg body byte offsets, per-run header byte offsets, per-msg
    line counts, non-body lines counted above each message).
  * `paint` — clone a chunk's cached `base` Text and layer the current
    cursor + selection spans on top.

All inputs are plain dicts and message dataclasses; all outputs are
Rich `Text` or simple dataclasses. No Textual import here — the units
are easy to unit-test without an `App.run_test()` harness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Iterable

from rich.console import Console
from rich.style import Style
from rich.text import Text

from ...models import TAPBACK_GLYPHS


# Speaker runs are interrupted by a same-speaker silence of ≥30 minutes;
# the next message starts a fresh run with its own header. Calendar-day
# boundaries always reset a run independent of this gap.
_RUN_TIME_GAP_MINUTES = 30

# Two-cell indent under the speaker header. Continuation lines (and
# wrapped continuations within a body line) align to a column derived
# from this plus the run's widest time label.
_BODY_INDENT = 2

# Short labels for known iMessage app payloads. Exact match wins first;
# any remaining bundle id starting with the generic extension-balloon
# prefix folds into a single "App message" label so unknown plugins
# don't render as a UUID-looking string.
_APP_BUNDLE_NAMES: dict[str, str] = {
    "com.apple.messages.URLBalloonProvider": "URL preview",
    "com.apple.PassbookUIService.PeerPaymentMessagesExtension": "Apple Pay",
    "com.apple.DigitalTouchBalloonProvider": "Digital Touch",
    "com.apple.Handwriting.HandwritingProvider": "Handwriting",
}
_APP_BUNDLE_PREFIX = "com.apple.messages.MSMessageExtensionBalloonPlugin"

# Cap the inline reaction snippet so a long target message doesn't blow
# the body column; ~40 cells matches Messages.app's own tapback summary.
_REACTION_SNIPPET_MAX = 40


class RowKind(Enum):
    """How a single message row should be rendered.

    NORMAL is the only kind that gets a plain body in default style;
    everything else renders as a single italic+muted footnote line so
    that reactions, app payloads, and empty-text rows visually fall
    out of the conversational flow.
    """
    NORMAL = auto()              # plain message with body text
    REACTION = auto()            # kind == "tapback"
    UNSENT = auto()              # kind == "unsent"
    APP = auto()                 # kind == "app"
    EMPTY_ATTACHMENT = auto()    # kind=="message", empty text, has_attachment
    EMPTY_NO_CONTENT = auto()    # kind=="message", empty text, no attachment


def classify(message) -> RowKind:
    """Map a Message to its RowKind in O(1).

    The branches are checked in priority order — `kind != "message"`
    flavors win immediately, then text-presence + has_attachment split
    the residual "message" rows. NORMAL also wins on
    text-with-attachment so a real reply doesn't get swallowed by an
    "(attachment)" footnote.
    """
    kind = (message.kind or "message")
    if kind == "tapback":
        return RowKind.REACTION
    if kind == "unsent":
        return RowKind.UNSENT
    if kind == "app":
        return RowKind.APP
    # kind == "message" (or any unknown future value): fall through to
    # text / attachment classification.
    if message.text:
        return RowKind.NORMAL
    if message.has_attachment:
        return RowKind.EMPTY_ATTACHMENT
    return RowKind.EMPTY_NO_CONTENT


# Per-module cache for Style.parse — formatter is called once per
# message per render, so memoizing the ~5 distinct style specs saves
# repeated parse work on cache builds.
_STYLE_CACHE: dict[str, Style] = {}


# Shared Console used only for `Text.wrap` line-count measurement.
# wrap() needs a Console but only reads `tab_size` etc — never prints,
# so a default no-color file=None console is fine. Reusing one instance
# saves ~20µs of Console() construction per format_row call.
_WRAP_CONSOLE = Console(file=None, force_terminal=False, color_system=None)


def _parse_style(spec: str) -> Style:
    cached = _STYLE_CACHE.get(spec)
    if cached is None:
        cached = Style.parse(spec) if spec else Style()
        _STYLE_CACHE[spec] = cached
    return cached


def _short_app_label(bundle: str | None) -> str | None:
    """Map an iMessage app bundle id to a human-readable short label,
    or None when there's no bundle to label.

    Exact match wins first; any bundle id matching the generic
    extension-balloon prefix folds into "App message"; anything else
    falls through to the bundle id verbatim.
    """
    if not bundle:
        return None
    named = _APP_BUNDLE_NAMES.get(bundle)
    if named is not None:
        return named
    if bundle.startswith(_APP_BUNDLE_PREFIX):
        return "App message"
    return bundle


def _render_body(message, kind: "RowKind") -> tuple[str, str]:
    """Return (body_text, style_spec) for a single message row.

    `style_spec` is the same string format `_parse_style` consumes
    ("" for default, "muted italic" for footnote rows, etc.) — the
    caller composes it onto the per-segment meta Style.

    Edited messages get an inline ` (edited)` suffix appended to the
    body, regardless of RowKind. The spec called for muted-non-italic
    styling on the marker itself; we keep it inline in the same body
    string to avoid splitting one logical body line into three style
    segments — wrap and offset math stays simpler, and the marker is
    visible enough at the end of a row.
    """
    if kind is RowKind.NORMAL:
        body = message.text or ""
        style_spec = ""
    elif kind is RowKind.REACTION:
        reaction = message.reaction or {}
        rtype = reaction.get("type") or ""
        glyph = TAPBACK_GLYPHS.get(rtype, "·")
        target = reaction.get("target_text") or ""
        if len(target) > _REACTION_SNIPPET_MAX:
            target = target[:_REACTION_SNIPPET_MAX] + "…"
        body = f'{glyph} to "{target}"'
        style_spec = "muted italic"
    elif kind is RowKind.UNSENT:
        body = "(unsent)"
        style_spec = "muted italic"
    elif kind is RowKind.APP:
        label = _short_app_label(message.app_bundle)
        body = f"({label} · app payload)" if label else "(app payload)"
        style_spec = "muted italic"
    elif kind is RowKind.EMPTY_ATTACHMENT:
        body = "(attachment)"
        style_spec = "muted italic"
    else:  # EMPTY_NO_CONTENT
        body = "(no content)"
        style_spec = "muted italic"

    if message.is_edited:
        body = f"{body} (edited)"
    return body, style_spec


def _format_time_12h(ts: str) -> str:
    hh = ts[11:13] if len(ts) >= 19 else ts[:2]
    mm = ts[14:16] if len(ts) >= 19 else ts[3:5]
    h = int(hh)
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{mm} {period}"


@dataclass(slots=True)
class _RunRender:
    """One rendered speaker run's segments plus the byte ranges the
    chunk builder needs to stitch it into `_ChunkRender.row_offsets`.

    Byte ranges are into the concatenated plain text of `segments`.
    The chunk builder shifts them by `len(base.plain)` before storing,
    so a run rendered in isolation can be unit-tested without the
    whole chunk surface.

    `header_range` is the speaker-line byte span. The chunk builder
    uses it to register a header lookup for run[0] so the cursor's
    highlight bar paints on the speaker line in addition to the body
    line (Edge cases: "cursor on header line vs body line"). Selection
    bg, by contrast, only uses `msg_body_ranges` — so range marks
    don't smear onto the speaker headers above them.
    """
    segments: list
    msg_body_ranges: dict
    header_range: tuple
    msg_line_counts: dict


def _semantic_style_to_hex(spec: str, palette: dict) -> str:
    """Translate a `_render_body` semantic style spec ("muted italic",
    "italic", "") into a Rich-parseable style string with the palette's
    hex substituted in for `muted`.

    Keeps `_render_body` palette-free (and trivially unit-testable)
    while still letting the renderer bake explicit palette hex into
    each segment's Style at build time.
    """
    if not spec:
        return ""
    out = []
    for token in spec.split():
        if token == "muted":
            hex_code = palette.get("muted", "")
            if hex_code:
                out.append(hex_code)
        else:
            out.append(token)
    return " ".join(out)


def _measure_body_rows(
    body_text: str,
    body_style: Style,
    indent_prefix: str,
    width: int | None,
) -> int:
    """Count the rendered terminal rows for a single body line.

    `body_text` is the post-indent body string (continuation \\n's already
    have indent appended). When `width` is None we fall back to the
    explicit-newlines count; otherwise we use Rich's actual wrap to
    catch URL-token wrap rows the arithmetic version misses.
    """
    if width is None or width <= 0:
        return 1 + body_text.count("\n")
    row_text = Text()
    row_text.append(indent_prefix, body_style)
    row_text.append(body_text, body_style)
    row_text.append("\n")
    wrapped = row_text.wrap(_WRAP_CONSOLE, width)
    # The trailing "\n" injects an empty final line that wrap counts as
    # its own row; subtract it.
    return max(1, len(wrapped) - 1)


def format_run(
    run: list,
    contacts: dict,
    *,
    width: int | None,
    suppress_leading_blank: bool,
    palette: dict,
) -> _RunRender:
    """Render one speaker run into segments + offsets + line counts.

    A "run" is a maximal sequence of consecutive messages by the same
    speaker, on the same calendar day, with no internal ≥30-min gap.
    Run detection happens in `_ChunkRender.build`; this function only
    renders.

    Emit shape (per spec):

      <blank line>?                ← omitted when suppress_leading_blank
      <speaker>  ·  <h:mm AM/PM>   ← header line, meta = run[0].id
      <2-cell indent><body>        ← run[0] body line(s)
      <2-cell indent><indent-time>  <body>   ← run[1] continuation
      <2-cell indent><indent-time>  <body>   ← run[2] continuation
      …

    Continuation lines (multi-message runs) align to a body column
    computed from the run's widest time label — see _BODY_COLUMN math
    in the multi-message branch.

    `palette` is required: speaker color (accent_alt for me, accent
    for them) and the footnote muted+italic color are baked into
    explicit Style hex at build time.
    """
    if not run:
        return _RunRender(
            segments=[], msg_body_ranges={},
            header_range=(0, 0), msg_line_counts={},
        )

    head = run[0]
    head_meta = Style(meta={"msg_id": head.message_id})
    speaker_color = palette.get(
        "accent_alt" if head.is_from_me else "accent", "")
    speaker_style = head_meta + _parse_style(
        f"bold {speaker_color}".strip())
    muted_hex = palette.get("muted", "")
    # `muted_base` has no meta — compose with the message-specific
    # meta at emit time so continuation time prefixes route to THEIR
    # own message id, not the run head's.
    muted_base = _parse_style(muted_hex)

    segments: list[tuple[str, Style]] = []
    cursor = 0

    def emit(text: str, style: Style) -> None:
        nonlocal cursor
        segments.append((text, style))
        cursor += len(text)

    if not suppress_leading_blank:
        # Sub-row furniture — meta-less so a click on it doesn't route
        # to any message id.
        emit("\n", Style())

    # Pre-pass: collect every time label in the run and find the
    # widest. Continuation lines right-pad to this width so all body
    # columns in the run align. Pre-passing once instead of measuring
    # mid-emit means a single max() over O(N) labels.
    run_times = [_format_time_12h(m.timestamp) for m in run]
    max_time_len = max(len(t) for t in run_times)
    continuation_prefix_len = _BODY_INDENT + max_time_len + 2

    header_start = cursor
    emit(head.author_label or "", speaker_style)
    emit("  ·  ", head_meta + muted_base)
    emit(run_times[0], head_meta + muted_base)
    emit("\n", head_meta)
    header_end = cursor

    indent = " " * _BODY_INDENT
    cont_wrap_indent = " " * continuation_prefix_len
    msg_body_ranges: dict[int, tuple[int, int]] = {}
    msg_line_counts: dict[int, int] = {}

    for i, msg in enumerate(run):
        msg_meta = Style(meta={"msg_id": msg.message_id})
        kind = classify(msg)
        body_text, style_spec = _render_body(msg, kind)
        body_style = msg_meta + _parse_style(
            _semantic_style_to_hex(style_spec, palette))

        body_start = cursor
        if i == 0:
            # run[0] body: just the 2-cell indent. The header above
            # already carries the time, so an inline time would be
            # redundant. Multi-line bodies wrap under the same indent.
            wrap_indent = indent
            rendered_body = body_text.replace("\n", "\n" + wrap_indent)
            emit(indent, msg_meta)
            emit(rendered_body, body_style)
        else:
            # Continuation: <indent><padded-time><2-space gap><body>.
            # The padded time carries this msg's meta so a click on the
            # time still drops a mark on its own message, not the head.
            padded_time = run_times[i].ljust(max_time_len)
            wrap_indent = cont_wrap_indent
            rendered_body = body_text.replace("\n", "\n" + wrap_indent)
            emit(indent, msg_meta)
            emit(padded_time, msg_meta + muted_base)
            emit("  ", msg_meta)
            emit(rendered_body, body_style)
        emit("\n", msg_meta)
        body_end = cursor

        msg_body_ranges[msg.message_id] = (body_start, body_end)
        msg_line_counts[msg.message_id] = _measure_body_rows(
            rendered_body, body_style, wrap_indent, width)

    return _RunRender(
        segments=segments,
        msg_body_ranges=msg_body_ranges,
        header_range=(header_start, header_end),
        msg_line_counts=msg_line_counts,
    )


def _gap_minutes(prev_ts: str, next_ts: str) -> float:
    """Wall-clock gap (minutes) between two `YYYY-MM-DD HH:MM:SS` stamps.

    Returns `inf` on a parse failure so an unknown format conservatively
    starts a new run rather than silently fusing rows together.
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        a = datetime.strptime(prev_ts, fmt)
        b = datetime.strptime(next_ts, fmt)
    except ValueError:
        return float("inf")
    return abs((b - a).total_seconds()) / 60.0


def _split_into_runs(msgs: list) -> list[list]:
    """Group adjacent messages into speaker runs.

    A new run starts when ANY of these is true vs. the previous message:
      - different `author_label`,
      - different calendar day,
      - same speaker but ≥ _RUN_TIME_GAP_MINUTES of silence between them.

    Single forward pass, O(N) with O(1) state.
    """
    if not msgs:
        return []
    runs: list[list] = [[msgs[0]]]
    for m in msgs[1:]:
        prev = runs[-1][-1]
        same_speaker = m.author_label == prev.author_label
        same_day = m.timestamp[:10] == prev.timestamp[:10]
        small_gap = _gap_minutes(prev.timestamp, m.timestamp) < _RUN_TIME_GAP_MINUTES
        if same_speaker and same_day and small_gap:
            runs[-1].append(m)
        else:
            runs.append([m])
    return runs


def _emit_day_header(
    base: Text,
    day: str,
    palette: dict,
    width: int | None,
) -> None:
    """Append the day separator (rule + label + rule) into `base`.

    Full-width rule when `width` is provided — total cell width equals
    `width` exactly, so the rule spans corner-to-corner of the viewport.
    Falls back to the short `── label ──` form when width isn't known
    yet (e.g. chunks built before first mount lay down geometry).

    Label uses the `day_header` palette color; rule chars use
    `border_soft` so the boundary reads as a quiet UI line rather than
    a strong divider.
    """
    dt = datetime.strptime(day, "%Y-%m-%d")
    label_inner = dt.strftime("%A, %B %-d, %Y")
    day_color = palette.get("day_header", "")
    rule_color = palette.get("border_soft", "")
    label_style = _parse_style(f"bold {day_color}".strip())
    rule_style = _parse_style(rule_color) if rule_color else Style()

    if width and width > 0:
        bordered_label = f"  {label_inner}  "
        rule_cells = max(0, width - len(bordered_label))
        left = rule_cells // 2
        right = rule_cells - left
        if left:
            base.append("─" * left, style=rule_style)
        base.append(bordered_label, style=label_style)
        if right:
            base.append("─" * right, style=rule_style)
        base.append("\n")
    else:
        # Short fallback — preserved so tests + cold-cache renders still
        # produce a recognizable day boundary.
        base.append(f"── {label_inner} ──\n", style=label_style)


@dataclass(slots=True)
class _ChunkRender:
    """One mounted chunk's cached render state.

    `widget` is filled in by HistoryView after mount — `build` returns
    the dataclass with `widget=None` and the caller stitches it on.
    Everything else is fully precomputed so paint() never needs to
    re-walk the messages.

    `row_offsets` brackets the BODY span only (indent + body + newline).
    `header_offsets` brackets the speaker header line, keyed by the run
    head's message id — paint() uses it to layer the cursor highlight
    on the header when the cursor is on run[0], so the highlight reads
    as "the cursor is on this message" rather than "the cursor is on
    this body line".
    """
    msg_ids: list[int]
    base: Text                                  # unstyled blob — build-once
    row_offsets: dict[int, tuple[int, int]]     # body byte span per msg
    header_offsets: dict[int, tuple[int, int]]  # speaker-line byte span, run heads only
    row_line_counts: dict[int, int]             # rendered terminal rows per msg body
    # Non-body lines rendered above msg_ids[i]: day headers, the blank
    # separator line that precedes a non-first day header, the blank
    # separator line before each non-first run on a day, and the
    # speaker header line for the run msg_ids[i] is in. Continuations
    # within a run share the same prefix count as their run head — the
    # caller adds per-msg row_line_counts on top to land cursor y.
    prefix_lines_above: list[int]
    widget: object | None = None                # Static; injected after mount

    @classmethod
    def build(
        cls,
        messages: Iterable,
        contacts: dict,
        *,
        palette: dict,
        width: int | None = None,
    ) -> "_ChunkRender":
        """Detect runs, emit day headers + speaker headers + bodies in
        one pass, and return the cached render state.

        `palette` is required — the speaker color, footnote muted+italic,
        and day-header/rule colors are baked into the Text at build time.
        Theme changes invalidate the cached base on next chat load; the
        rest of the surface (Textual CSS-driven) updates immediately.

        `width`, when provided, makes per-row line counts wrap-aware AND
        promotes the day header to full-width form.
        """
        msgs = list(messages)
        base = Text()
        msg_ids: list[int] = []
        row_offsets: dict[int, tuple[int, int]] = {}
        header_offsets: dict[int, tuple[int, int]] = {}
        row_line_counts: dict[int, int] = {}
        prefix_lines_above: list[int] = []

        if not msgs:
            return cls(
                msg_ids=msg_ids, base=base,
                row_offsets=row_offsets,
                header_offsets=header_offsets,
                row_line_counts=row_line_counts,
                prefix_lines_above=prefix_lines_above,
            )

        runs = _split_into_runs(msgs)
        last_date: str | None = None
        prefix_so_far = 0

        for run in runs:
            run_day = run[0].timestamp[:10]
            first_run_of_day = run_day != last_date

            if first_run_of_day:
                if last_date is not None:
                    # Blank separator line before each non-first day
                    # header — keeps day rules from butting up against
                    # the previous run's last body line.
                    base.append("\n")
                    prefix_so_far += 1
                _emit_day_header(base, run_day, palette, width)
                prefix_so_far += 1
                last_date = run_day

            # The first run after a day header omits its own leading
            # blank — the day rule already provided space. Subsequent
            # runs on the same day get the blank back via format_run.
            suppress_leading_blank = first_run_of_day
            if not suppress_leading_blank:
                prefix_so_far += 1  # the blank line format_run will emit
            # The speaker header line that format_run will emit:
            prefix_so_far += 1

            run_start = len(base.plain)
            run_render = format_run(
                run, contacts,
                width=width,
                suppress_leading_blank=suppress_leading_blank,
                palette=palette,
            )
            for text, style in run_render.segments:
                base.append(text, style=style)

            hs, he = run_render.header_range
            header_offsets[run[0].message_id] = (run_start + hs, run_start + he)

            for msg in run:
                bs, be = run_render.msg_body_ranges[msg.message_id]
                row_offsets[msg.message_id] = (run_start + bs, run_start + be)
                row_line_counts[msg.message_id] = (
                    run_render.msg_line_counts[msg.message_id])
                msg_ids.append(msg.message_id)
                # Continuations share the run head's prefix count —
                # only the run head sat under a new speaker header; the
                # caller adds prev msgs' row_line_counts to get screen-y.
                prefix_lines_above.append(prefix_so_far)

        return cls(
            msg_ids=msg_ids,
            base=base,
            row_offsets=row_offsets,
            header_offsets=header_offsets,
            row_line_counts=row_line_counts,
            prefix_lines_above=prefix_lines_above,
        )


@dataclass(slots=True, frozen=True)
class SelectionColors:
    """Hex codes consumed by `paint`. Empty strings mean "skip this
    layer" — keeps `paint` defensive against partial palettes.

    The cursor bar has three context-dependent colors because the
    default `accent_alt` would be invisible against an endpoint row
    (already `accent_alt` bg). On an in-range row (already `accent` bg)
    the bar reverts to `accent_alt`.
    """
    endpoint_bg: str
    range_bg: str
    cursor_tint_bg: str            # B — subtle row tint
    cursor_bar_default: str        # D — bar color on unselected rows
    cursor_bar_on_endpoint: str    # D — bar color over an endpoint bg
    cursor_bar_on_in_range: str    # D — bar color over an in-range bg
    contrast_fg: str               # text fg used on selection-bg rows


def selection_colors(palette: dict) -> SelectionColors:
    """Read the five palette keys we need and pack into SelectionColors."""
    accent = palette.get("accent", "")
    accent_alt = palette.get("accent_alt", "")
    return SelectionColors(
        endpoint_bg=accent_alt,
        range_bg=accent,
        cursor_tint_bg=palette.get("bg_alt", ""),
        cursor_bar_default=accent_alt,
        cursor_bar_on_endpoint=accent,
        cursor_bar_on_in_range=accent_alt,
        contrast_fg=palette.get("bg", ""),
    )


@dataclass(slots=True, frozen=True)
class MarkState:
    """The three pieces of selection state HistoryView passes to paint.

    Frozen + hashable so HistoryView (or anyone else) can short-circuit
    paint calls when state didn't change since the last paint.
    """
    anchor_id: int | None
    active_id: int | None
    in_range_ids: frozenset[int]


_EMPTY_MARKS = MarkState(None, None, frozenset())


def paint(
    chunk: _ChunkRender,
    marks: MarkState,
    palette: dict,
) -> Text:
    """Clone the chunk's cached base and overlay selection spans.

    The cached `base` is never mutated — every paint returns a clone.
    Endpoint background always wins; in-range layers under it. No
    cursor visual — viewport scroll position is the only "where am I"
    indicator in the viewport-only navigation model.
    """
    out = chunk.base.copy()
    colors = selection_colors(palette)
    endpoints = {marks.anchor_id, marks.active_id} - {None}

    for msg_id in chunk.msg_ids:
        start, end = chunk.row_offsets[msg_id]
        is_endpoint = msg_id in endpoints
        is_in_range = (not is_endpoint) and msg_id in marks.in_range_ids

        if is_endpoint and colors.endpoint_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.endpoint_bg}"),
                start, end,
            )
        elif is_in_range and colors.range_bg and colors.contrast_fg:
            out.stylize(
                _parse_style(f"{colors.contrast_fg} on {colors.range_bg}"),
                start, end,
            )

    return out
