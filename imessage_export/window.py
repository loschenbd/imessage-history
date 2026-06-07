"""Time-window resolution.

`TimeWindow` is the resolved bounds object embedded in every export's
metadata. `resolve_window` takes the parsed argparse `args` and the
detected timestamp unit ('ns' or 's') and produces the Apple-epoch
integers used by the SQL `WHERE m.date >= ? AND m.date < ?` clauses.

The CLI accepts three overlapping vocabularies (--start-datetime/
--end-datetime, --date+--start-time/--end-time, --from-date/--to-date).
`resolve_window` implements that precedence in one place so writers and
the export pipeline don't have to.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .timestamps import attach_local_tz, local_dt_to_apple


@dataclass
class TimeWindow:
    apple_start: Optional[int]
    apple_end: Optional[int]            # exclusive upper bound
    local_start: Optional[str]
    local_end: Optional[str]
    utc_start: Optional[str]
    utc_end: Optional[str]
    tz: str
    input: dict


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def parse_time(s: str) -> datetime:
    # Accept HH:MM or HH:MM:SS
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid time {s!r}; expected HH:MM or HH:MM:SS")


def parse_datetime(s: str) -> datetime:
    # Accept 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD HH:MM:SS' / ISO 'YYYY-MM-DDTHH:MM:SS'
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime {s!r}")


def resolve_window(args, unit: str) -> TimeWindow:
    """Resolve the time-window arguments into Apple-epoch bounds.

    Precedence:
      1) --start-datetime / --end-datetime
      2) --date + --start-time / --end-time
      3) --from-date / --to-date (existing day-granularity flags)
    """
    local_tz = datetime.now().astimezone().tzinfo
    tz_name = str(local_tz)
    input_record = {
        "from_date": args.from_date,
        "to_date": args.to_date,
        "date": args.date,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "start_datetime": args.start_datetime,
        "end_datetime": args.end_datetime,
    }

    start_local = end_local = None

    if args.start_datetime or args.end_datetime:
        if args.start_datetime:
            start_local = attach_local_tz(parse_datetime(args.start_datetime))
        if args.end_datetime:
            end_local = attach_local_tz(parse_datetime(args.end_datetime))

    elif args.date and (args.start_time or args.end_time):
        day = parse_date(args.date)
        if args.start_time:
            t = parse_time(args.start_time)
            start_local = attach_local_tz(day.replace(
                hour=t.hour, minute=t.minute, second=t.second))
        if args.end_time:
            t = parse_time(args.end_time)
            end_local = attach_local_tz(day.replace(
                hour=t.hour, minute=t.minute, second=t.second))

    elif args.date:
        # Entire day, local
        day = parse_date(args.date)
        start_local = attach_local_tz(day)
        end_local = attach_local_tz(day + timedelta(days=1))

    else:
        if args.from_date:
            start_local = attach_local_tz(parse_date(args.from_date))
        if args.to_date:
            # to_date is inclusive of that calendar day → upper bound is next day 00:00
            end_local = attach_local_tz(parse_date(args.to_date) + timedelta(days=1))

    apple_start = local_dt_to_apple(start_local, unit) if start_local else None
    apple_end = local_dt_to_apple(end_local, unit) if end_local else None

    return TimeWindow(
        apple_start=apple_start,
        apple_end=apple_end,
        local_start=start_local.strftime("%Y-%m-%d %H:%M:%S") if start_local else None,
        local_end=end_local.strftime("%Y-%m-%d %H:%M:%S") if end_local else None,
        utc_start=start_local.astimezone(timezone.utc).isoformat() if start_local else None,
        utc_end=end_local.astimezone(timezone.utc).isoformat() if end_local else None,
        tz=tz_name,
        input=input_record,
    )
