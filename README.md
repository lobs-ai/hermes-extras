# hermes-extras

Rafe's custom [Hermes](https://hermes-agent.nousresearch.com) plugins. Everything
here was previously shell scripts in `~/bin` authenticating against a Crew-owned
Google grant; this repo is the migration onto Hermes' own plugin system and its
own OAuth token.

## Why this exists

Hermes ships a `google-workspace` skill that already covers Gmail, Calendar,
Drive, Sheets and Docs. What it does not have is the small amount of *Rafe-
specific* logic layered on top:

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

Auth comes from Hermes' own `google-workspace` skill — `~/.hermes/google_token.json`.
There is no separate credential for this plugin, and it never reads the Crew keychain.

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

The distinction that earns its keep is `teaching` / `student` / `deadline`: on a
GSI's calendar those read identically in text and mean three different things.

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
