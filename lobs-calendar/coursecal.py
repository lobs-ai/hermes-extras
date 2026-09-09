"""Idempotent spec-to-calendar reconciliation.

The property that makes a weekly cron safe: every event written here carries
private extended properties ``lobscal=1`` and ``key=<stable key>``. A sync
lists everything with that stamp and reconciles create / update / delete
against the spec, so re-running changes nothing, a moved date moves the event
rather than duplicating it, and hand-made events are never touched.

Spec format (a JSON file, either a bare list or ``{"events": [...]}``)::

    {"events": [
       {"key": "eecs489:p1", "course": "EECS 489", "title": "P1 due",
        "date": "2026-09-21", "color": "deadline",
        "source": "https://www.eecs489.org/"},

       {"key": "eecs489:final", "course": "EECS 489", "title": "Final Exam",
        "start": "2026-12-17T10:30", "end": "2026-12-17T12:30",
        "color": "exam", "location": "TBD", "notes": "25% of grade."}
    ]}
"""

import datetime
import json

from . import colors

MARK = "lobscal"
# The stamp used by the ~/bin/coursecal shell script this plugin replaces.
# Events written before the migration carry it, and a sync that did not know
# about it would see zero existing events and duplicate every one of them.
LEGACY_MARKS = ("coursecal",)
TZ = "America/New_York"


class SpecError(ValueError):
    """The spec file is malformed."""


def load(path):
    with open(path) as fh:
        raw = json.load(fh)
    events = raw["events"] if isinstance(raw, dict) else raw
    if not isinstance(events, list):
        raise SpecError("spec must be a list of events, or {'events': [...]}")
    keys = [e.get("key") for e in events]
    if not all(keys):
        raise SpecError("every event needs a stable 'key'")
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise SpecError(f"duplicate keys: {', '.join(sorted(dupes))}")
    return events


def build(spec):
    """Spec entry -> a Google event resource."""
    title = spec["title"]
    course = spec.get("course")
    if course and not title.startswith(course):
        title = f"{course}: {title}"

    event = {
        "summary": title,
        "colorId": colors.infer(spec),
        "extendedProperties": {"private": {MARK: "1", "key": spec["key"]}},
        # Transparent: a deadline should not make him look busy to anyone
        # reading free/busy.
        "transparency": "transparent",
        "reminders": {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": 60 * 24},
            {"method": "popup", "minutes": 60 * 2},
        ]},
    }

    if spec.get("date"):
        end_date = spec.get("end_date") or spec["date"]
        year, month, day = map(int, end_date.split("-"))
        # Google's all-day end date is exclusive.
        following = (datetime.date(year, month, day)
                     + datetime.timedelta(days=1)).isoformat()
        event["start"] = {"date": spec["date"]}
        event["end"] = {"date": following}
    elif spec.get("start") and spec.get("end"):
        event["start"] = {"dateTime": spec["start"] + ":00", "timeZone": TZ}
        event["end"] = {"dateTime": spec["end"] + ":00", "timeZone": TZ}
    else:
        raise SpecError(f"{spec['key']}: needs either 'date' or 'start'+'end'")

    if spec.get("location"):
        event["location"] = spec["location"]
    description = spec.get("notes", "")
    if spec.get("source"):
        description = (description + f"\n\nsource: {spec['source']}\n"
                       "managed by lobs-calendar — edits here are overwritten").strip()
    if description:
        event["description"] = description
    return event


def matches(live, wanted):
    """Is the live event already what we want?"""
    if live.get("summary") != wanted["summary"]:
        return False
    if (live.get("colorId") or "") != (wanted.get("colorId") or ""):
        return False
    for side in ("start", "end"):
        have, want = live.get(side, {}), wanted[side]
        if "date" in want:
            if have.get("date") != want["date"]:
                return False
        elif (have.get("dateTime") or "")[:16] != want["dateTime"][:16]:
            return False
    if (live.get("location") or "") != (wanted.get("location") or ""):
        return False
    if (live.get("description") or "") != (wanted.get("description") or ""):
        return False
    return True


def plan(calendar, events, window_days=400):
    """Work out create / update / delete without touching anything.

    Reads events stamped by this plugin AND by any legacy stamp, so a migration
    adopts existing events instead of duplicating them. An adopted event is
    re-stamped on its first update.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = (now - datetime.timedelta(days=window_days)).isoformat()
    time_max = (now + datetime.timedelta(days=window_days)).isoformat()

    by_key, adopted = {}, set()
    for mark in (MARK,) + LEGACY_MARKS:
        for event in calendar.list_events(time_min, time_max,
                                          private_property=f"{mark}=1"):
            private = event.get("extendedProperties", {}).get("private", {})
            key = private.get("key")
            if not key or key in by_key:
                continue
            by_key[key] = event
            if mark != MARK:
                adopted.add(key)

    creates, updates = [], []
    for spec in events:
        wanted = build(spec)
        existing = by_key.pop(spec["key"], None)
        if existing is None:
            creates.append((spec["key"], wanted))
        elif spec["key"] in adopted or not matches(existing, wanted):
            # An adopted event is always rewritten once, to move it onto the
            # current stamp. Its content may well be identical.
            updates.append((spec["key"], existing["id"], wanted, existing))
    deletes = [(key, event) for key, event in by_key.items()]
    return creates, updates, deletes, adopted


def describe(event):
    start = event.get("start", {})
    return start.get("date") or (start.get("dateTime") or "")[:16]


def sync(calendar, events, apply=False):
    """Reconcile. ``apply=False`` is a dry run and is the default on purpose."""
    creates, updates, deletes, adopted = plan(calendar, events)
    actions = []
    for key, wanted in creates:
        actions.append({"action": "create", "key": key,
                        "when": describe(wanted), "summary": wanted["summary"]})
        if apply:
            calendar.create(wanted)
    for key, event_id, wanted, existing in updates:
        action = "adopt" if key in adopted else "update"
        entry = {"action": action, "key": key,
                 "when": describe(wanted), "summary": wanted["summary"]}
        was = describe(existing)
        if was != describe(wanted):
            entry["was"] = was
        actions.append(entry)
        if apply:
            calendar.patch(event_id, wanted)
    for key, event in deletes:
        actions.append({"action": "delete", "key": key,
                        "when": describe(event),
                        "summary": event.get("summary", "")})
        if apply:
            calendar.delete(event["id"])
    return {
        "applied": apply,
        "created": len(creates),
        "updated": len(updates),
        "deleted": len(deletes),
        "adopted": len(adopted),
        "actions": actions,
    }
