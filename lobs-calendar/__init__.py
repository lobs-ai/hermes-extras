"""lobs-calendar — Rafe's calendar layer over the google-workspace skill.

Registers four tools and a `hermes lobs-calendar` CLI command group. Auth is
Hermes' own Google token; this plugin holds no credential of its own.
"""

import json

from . import colors
from .coursecal import load as load_spec
from .coursecal import sync as sync_spec
from .google import Calendar, CalendarError

# "Lobs Planning" — the bot-owned calendar Rafe subscribes to. His own
# rsymonds@umich.edu is reader-only for this grant and a POST there 403s.
DEFAULT_CALENDAR = (
    "78a805eebb268c707d8c488f5b2579eb085db6dbb1e2c9a883df189f09734dc1"
    "@group.calendar.google.com"
)

TZ = "America/New_York"


def _ok(**payload):
    return json.dumps({"success": True, **payload}, indent=2)


def _err(message, **extra):
    return json.dumps({"success": False, "error": message, **extra}, indent=2)


def _calendar(params):
    return Calendar(params.get("calendar") or DEFAULT_CALENDAR)


# --------------------------------------------------------------------- tools

def _handle_create(params, **_):
    """Create an event, optionally recurring, with a category colour."""
    try:
        cal = _calendar(params)
        event = {
            "summary": params["summary"],
            "start": {"dateTime": params["start"], "timeZone": TZ},
            "end": {"dateTime": params["end"], "timeZone": TZ},
        }
        if params.get("description"):
            event["description"] = params["description"]
        if params.get("location"):
            event["location"] = params["location"]
        if params.get("color"):
            event["colorId"] = colors.resolve(params["color"])

        if params.get("recur"):
            rule = params["recur"]
            if not rule.upper().startswith("FREQ="):
                rule = "FREQ=" + rule
            if params.get("until"):
                # RFC5545 UNTIL must be UTC for a zoned DTSTART. Push to the end
                # of the local day so an inclusive date behaves inclusively.
                import datetime
                import zoneinfo
                year, month, day = map(int, params["until"].split("-"))
                local_end = datetime.datetime(year, month, day, 23, 59, 59,
                                              tzinfo=zoneinfo.ZoneInfo(TZ))
                rule += ";UNTIL=" + local_end.astimezone(
                    datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            event["recurrence"] = ["RRULE:" + rule]
            if params.get("exclude"):
                stamp = params["start"][11:16].replace(":", "") + "00"
                dates = [d.strip().replace("-", "") + "T" + stamp
                         for d in params["exclude"] if d.strip()]
                event["recurrence"].append(f"EXDATE;TZID={TZ}:" + ",".join(dates))

        created = cal.create(event)
        return _ok(id=created["id"], summary=created.get("summary"),
                   colorId=created.get("colorId"),
                   recurrence=created.get("recurrence"),
                   link=created.get("htmlLink"))
    except (CalendarError, colors.UnknownColor, KeyError) as exc:
        return _err(str(exc))


def _handle_recolor(params, **_):
    """Recolour one event. On a recurring master, every instance follows."""
    try:
        cal = _calendar(params)
        color_id = colors.resolve(params["color"])
        # Patching an instance id colours only that instance; patching the
        # master colours the series. Callers usually mean the series.
        event_id = params["event_id"].split("_")[0] if params.get("whole_series", True) \
            else params["event_id"]
        updated = cal.patch(event_id, {"colorId": color_id})
        return _ok(id=updated["id"], summary=updated.get("summary"),
                   colorId=updated.get("colorId"),
                   category=colors.category_of(updated.get("colorId")))
    except (CalendarError, colors.UnknownColor, KeyError) as exc:
        return _err(str(exc))


def _handle_audit(params, **_):
    """List events in a window with their colour category, flagging uncoloured ones."""
    try:
        cal = _calendar(params)
        events = cal.list_events(params["start"], params["end"])
        rows, uncolored = [], []
        for event in events:
            category = colors.category_of(event.get("colorId"))
            row = {
                "id": event["id"],
                "summary": event.get("summary", ""),
                "when": (event.get("start", {}).get("date")
                         or (event.get("start", {}).get("dateTime") or "")[:16]),
                "category": category or "NONE",
            }
            rows.append(row)
            if not category:
                uncolored.append(row)
        counts = {}
        for row in rows:
            counts[row["category"]] = counts.get(row["category"], 0) + 1
        return _ok(total=len(rows), by_category=counts,
                   uncolored=uncolored, events=rows)
    except CalendarError as exc:
        return _err(str(exc))


def _handle_sync(params, **_):
    """Reconcile a JSON deadline spec into the calendar. Dry run by default."""
    try:
        cal = _calendar(params)
        events = load_spec(params["spec"])
        result = sync_spec(cal, events, apply=bool(params.get("apply")))
        return _ok(**result)
    except (CalendarError, ValueError, OSError, KeyError) as exc:
        return _err(str(exc))


_CAL_ARG = {
    "type": "string",
    "description": "Calendar id. Defaults to Lobs Planning, the one Hermes can write.",
}

_SCHEMAS = [
    ({
        "name": "calendar_create_event",
        "description": (
            "Create a Google Calendar event with a colour category, optionally "
            "recurring. Colours: lecture, teaching, deadline, student, exam, "
            "esports, meeting, personal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start": {"type": "string",
                          "description": "ISO 8601 with offset, e.g. 2026-09-10T10:30:00-04:00"},
                "end": {"type": "string", "description": "ISO 8601 with offset"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "color": {"type": "string",
                          "description": "Category, Google colour name, or 1-11"},
                "recur": {"type": "string",
                          "description": "RRULE body, e.g. 'WEEKLY;BYDAY=TU,TH'"},
                "until": {"type": "string",
                          "description": "Inclusive last date for --recur, YYYY-MM-DD"},
                "exclude": {"type": "array", "items": {"type": "string"},
                            "description": "YYYY-MM-DD dates to skip (breaks, holidays)"},
                "calendar": _CAL_ARG,
            },
            "required": ["summary", "start", "end"],
        },
    }, _handle_create, "calendar_create_event"),

    ({
        "name": "calendar_recolor_event",
        "description": (
            "Change one event's colour category without recreating it. "
            "Recolours the whole series by default when given an instance id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "color": {"type": "string",
                          "description": "Category, Google colour name, or 1-11"},
                "whole_series": {"type": "boolean",
                                 "description": "Default true — patch the recurring master"},
                "calendar": _CAL_ARG,
            },
            "required": ["event_id", "color"],
        },
    }, _handle_recolor, "calendar_recolor_event"),

    ({
        "name": "calendar_audit_colors",
        "description": (
            "List events in a window with their colour category and a count per "
            "category, flagging any that carry no category. Use this to check the "
            "scheme is applied consistently."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "ISO 8601 with offset"},
                "end": {"type": "string", "description": "ISO 8601 with offset"},
                "calendar": _CAL_ARG,
            },
            "required": ["start", "end"],
        },
    }, _handle_audit, "calendar_audit_colors"),

    ({
        "name": "calendar_sync_spec",
        "description": (
            "Reconcile a JSON deadline spec into the calendar: create, move and "
            "delete stamped events to match. Idempotent — re-running is a no-op, "
            "hand-made events are never touched. Dry run unless apply is true."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "Path to the JSON spec file"},
                "apply": {"type": "boolean",
                          "description": "Default false. An unexplained delete in a "
                                         "dry run means stop, not apply."},
                "calendar": _CAL_ARG,
            },
            "required": ["spec"],
        },
    }, _handle_sync, "calendar_sync_spec"),
]


# ----------------------------------------------------------------------- CLI

def _cli_setup(parser):
    sub = parser.add_subparsers(dest="lobscal_cmd", required=True)
    sub.add_parser("colors", help="Show the colour scheme")

    audit = sub.add_parser("audit", help="Colour audit over a window")
    audit.add_argument("--start", required=True)
    audit.add_argument("--end", required=True)
    audit.add_argument("--calendar", default=DEFAULT_CALENDAR)

    sync = sub.add_parser("sync", help="Reconcile a deadline spec")
    sync.add_argument("spec")
    sync.add_argument("--apply", action="store_true")
    sync.add_argument("--calendar", default=DEFAULT_CALENDAR)

    share = sub.add_parser(
        "sharing",
        help="Show who the calendar is shared with (needs the full calendar scope)")
    share.add_argument("--calendar", default=DEFAULT_CALENDAR)


def _cli_handle(args):
    if args.lobscal_cmd == "colors":
        print(f"{'id':<4} {'google name':<12} category")
        for cid, name, category in colors.table():
            print(f"{cid:<4} {name:<12} {category or '-'}")
        return 0

    if args.lobscal_cmd == "audit":
        out = _handle_audit({"start": args.start, "end": args.end,
                             "calendar": args.calendar})
        print(out)
        return 0 if json.loads(out)["success"] else 1

    if args.lobscal_cmd == "sync":
        out = _handle_sync({"spec": args.spec, "apply": args.apply,
                            "calendar": args.calendar})
        payload = json.loads(out)
        if not payload["success"]:
            print(payload["error"])
            return 1
        for action in payload["actions"]:
            was = f"   (was {action['was']})" if action.get("was") else ""
            print(f"  {action['action'].upper():<7} {action['when']:<18} "
                  f"{action['summary']}{was}")
        verb = "applied" if payload["applied"] else "dry run"
        print(f"\n{verb}. {payload['created']} create, {payload['updated']} update, "
              f"{payload['deleted']} delete"
              + ("" if payload["applied"] else ". re-run with --apply"))
        return 0

    if args.lobscal_cmd == "sharing":
        try:
            rules = Calendar(args.calendar).acl()
        except CalendarError as exc:
            print(f"{exc}\n\nNote: reading sharing needs the full 'calendar' scope; "
                  "'calendar.events' returns 403.")
            return 1
        print("Per-event colours only render for accounts with a writer role "
              "or better.\n")
        for rule in rules:
            scope = rule.get("scope", {})
            print(f"  {rule.get('role','?'):<12} {scope.get('type','?')}: "
                  f"{scope.get('value','')}")
        return 0
    return 1


def register(ctx):
    for schema, handler, name in _SCHEMAS:
        ctx.register_tool(name=name, toolset="lobs_calendar",
                          schema=schema, handler=handler)
    ctx.register_cli_command(
        name="lobs-calendar",
        help="Rafe's calendar colour scheme and deadline sync",
        setup_fn=_cli_setup,
        handler_fn=_cli_handle,
    )
