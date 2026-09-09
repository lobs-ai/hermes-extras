# hermes-extras

[Hermes](https://hermes-agent.nousresearch.com) plugins built for a specific
workflow — a student-and-course-staff calendar — and published in case the shape
is useful to someone else.

## Why this exists

Hermes ships a `google-workspace` skill that already covers Gmail, Calendar,
Drive, Sheets and Docs. What it does not have is the small amount of
*opinionated* logic layered on top:

- a **named colour scheme** so a glance at the week distinguishes a class he
  attends from a course he staffs from a student deadline that predicts his
  support load;
- an **idempotent spec-to-calendar sync**, so a scheduled job can re-scrape
  course sites weekly and reconcile without ever creating a duplicate.

Per the Hermes contribution rubric, that is plugin-shaped rather than
core-shaped: it is user-specific, and it composes existing capability rather
than widening the tool schema.

## Layout

```
lobs-calendar/          # the plugin
  plugin.yaml
  __init__.py           # register() — tools + CLI commands
  colors.py             # the category scheme (single source of truth)
  google.py             # thin Calendar client over Hermes' google_token.json
  coursecal.py          # idempotent spec -> calendar reconciliation
```

## Install

```sh
hermes plugins install lobs-ai/hermes-extras
hermes plugins enable lobs-calendar
```

Auth comes from Hermes' own `google-workspace` skill —
`$HERMES_HOME/google_token.json`. This plugin holds no credential of its own,
which means setup, refresh and revocation all stay in one place.

Set the calendar it writes to in `config.yaml`; unset, it uses your primary
calendar:

```yaml
lobs_calendar:
  calendar_id: abc123@group.calendar.google.com
```

Every tool and CLI command also takes an explicit calendar argument, which wins
over the config.

## Colour scheme

| Category | Colour | id | Means |
|---|---|---|---|
| `lecture` | blueberry | 9 | a class he attends |
| `teaching` | grape | 3 | a course he staffs |
| `deadline` | tomato | 11 | his own due dates |
| `student` | tangerine | 6 | student deadlines — support load, not homework |
| `exam` | flamingo | 4 | exams |
| `esports` | basil | 10 | Rocket League |
| `meeting` | peacock | 7 | standing meetings |
| `personal` | sage | 2 | everything else |

The distinction that earns its keep is `teaching` / `student` / `deadline`. On
the calendar of someone who both takes courses and staffs them, those read
identically in text and mean three different things: a course you teach, a date
that predicts your support load, and your own work.

> **Per-event colours are only visible to accounts with write access to the
> calendar.** A subscriber with "See all event details" sees the whole calendar
> in one flat colour, whatever each event's `colorId` says. If the colours look
> uniform, that is a sharing permission, not a bug in this code.

## Idempotency

Every event this plugin writes carries private extended properties
`lobscal=1` and `key=<stable key>`. A sync lists everything with that stamp and
reconciles against the spec:

- re-running is a no-op;
- changing a date **moves** the event rather than making a second one;
- removing an entry deletes the event;
- unstamped events are never touched, so hand-made events are safe;
- `apply=false` (the default) is a dry run.

An unexplained DELETE in a dry run means stop, not apply — it usually means a
scrape failed and returned an empty page, not that a deadline was cancelled.

## Migrating an existing stamped calendar

The plugin identifies its own events by a private extended property
(`lobscal=1`). If you are moving from another tool that stamped events with a
different key, add it to `LEGACY_MARKS` in `coursecal.py` **before the first
sync**. Otherwise the reconciler sees none of your existing events and creates a
duplicate of every one.

With a legacy mark declared, a first sync reports `adopt` rather than `create`,
rewrites each event once onto the current stamp, and is a clean no-op from then
on.

This path was exercised on a real 12-event migration: the dry run reported
0 create / 12 adopt, the apply re-stamped all 12, the re-run was a no-op, and a
term-wide audit found no duplicates and nothing uncoloured. Create with
recurrence and exclusions, recolour, and delete were round-tripped against the
live API.

## Status

Working, and in daily use against a real calendar. No test suite yet — the
verification above was manual against the live API.
