# hermes-extras

[Hermes](https://hermes-agent.nousresearch.com) plugins built for one person's
setup and published in case the shape is useful to someone else: a
student-and-course-staff calendar, a list of the web services running on an
always-on machine, and a memory tweak.

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
lobs-calendar/            # calendar colour scheme + deadline sync
  plugin.yaml
  __init__.py             # register() — tools + CLI commands
  colors.py               # the category scheme (single source of truth)
  google.py               # thin Calendar client over Hermes' google_token.json
  coursecal.py            # idempotent spec -> calendar reconciliation
tailnet-services/         # web services on this machine, listed in the desktop app
  plugin.yaml
  __init__.py             # register() — the `hermes tailnet-services` CLI, no tools
  registry.py             # JSON registry under $HERMES_HOME/plugin-data/
  tailnet.py              # tailscale serve, lsof binding check, health probe
  dashboard/
    manifest.json         # mounts the API; tab hidden, the web dashboard shows nothing
    plugin_api.py         # GET /api/plugins/tailnet-services/services
    dist/index.js         # empty stub so the web dashboard doesn't flag a missing bundle
  desktop/
    plugin.js             # the Services page in the desktop app sidebar
hindsight-primary-only/   # skip Hindsight auto-retain outside primary sessions
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
>
> ```sh
> hermes lobs-calendar sharing            # who holds what role
> hermes lobs-calendar share you@example.com   # promote to writer; colours appear
> ```
>
> Both need the full `calendar` OAuth scope; `calendar.events` returns 403 on
> the ACL endpoints.

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

## tailnet-services

An always-on machine collects web services: a search backend in Docker, a
home page, a side project on some port. After a month nobody remembers which
ones are running or where. This plugin keeps a list of them, makes each one
reachable from the rest of the tailnet, and shows the list on a Services page
in the Hermes desktop app with a health dot, the URL, a description, the
source repo and an Open button.

The list only holds what the agent registers on purpose. Nothing is
auto-discovered, so an entry means someone decided it was worth keeping.

### Registering a service

```sh
hermes tailnet-services add grafana --port 3000 -d "Metrics dashboards"
hermes tailnet-services add notes --port 8080 --repo owner/notes -d "Notes app"
hermes tailnet-services ls          # with a live health probe
hermes tailnet-services check       # exits 1 if anything is down or unserved
hermes tailnet-services rm notes
```

`add` checks how the port is bound with `lsof`:

- **Loopback only** (`127.0.0.1`): runs
  `tailscale serve --bg --https=<port> http://127.0.0.1:<port>`, so the service
  becomes `https://<machine>.<tailnet>.ts.net:<port>/`, reachable from the
  tailnet and nowhere else. `rm` runs `tailscale serve --https=<port> off`.
- **All interfaces**: nothing to serve, and a serve on the same port would
  collide with it. The URL is `http://<machine>.<tailnet>.ts.net:<port>/`.
- **Nothing listening**: refused, so the list never starts out wrong. Pass
  `--mode serve` or `--mode direct` to register ahead of time.

A serve that already existed before `add` is recorded as such, and `rm` leaves
it alone. The tailnet DNS name comes from `tailscale status --json` on every
call and is never stored. The registry lives at
`$HERMES_HOME/plugin-data/tailnet-services/services.json`, written atomically.

There are no model tools. The agent uses the CLI from its terminal like a
person would, which keeps the tool schema unchanged.

### Health

The desktop page calls `GET /api/plugins/tailnet-services/services`, which
probes `http://127.0.0.1:<port>/` on the backend machine with a 2 second
timeout. Any HTTP response, a 404 or a 500 included, counts as up: the dot
answers "is something listening", not "is it healthy". The page refetches
every 15 seconds.

Open uses `host.openPreview(url, name)` if the desktop app provides it, and
otherwise opens the system browser through `ctx.os.openExternal`.

### Install

The Python half and the dashboard API run on the machine with the services:

```sh
hermes plugins install lobs-ai/hermes-extras      # or `hermes plugins update hermes-extras`
hermes plugins enable tailnet-services
```

Because this repo is a monorepo, the plugin sits one level down at
`plugins/hermes-extras/tailnet-services/`. The CLI loader finds it there and
enables it under the key `hermes-extras/tailnet-services`. The dashboard only
scans `plugins/*/dashboard/manifest.json` and checks the bare plugin name, so
two more steps are needed before the API mounts:

```sh
# expose only the dashboard/ folder at the top level, so the CLI half isn't found twice
mkdir -p ~/.hermes/plugins/tailnet-services
ln -s ../hermes-extras/tailnet-services/dashboard ~/.hermes/plugins/tailnet-services/dashboard

# the dashboard API gate wants the bare name in plugins.enabled next to the key:
# pass your existing list plus "tailnet-services"
hermes config get plugins.enabled
hermes config set plugins.enabled '["hermes-extras/tailnet-services", "tailnet-services", ...]'
```

Then restart the dashboard backend once. Plugin API routes mount only at
startup.

The desktop half is installed from the desktop app: **Capabilities → Plugins →
Install from Git**, identifier `lobs-ai/hermes-extras/tailnet-services`, with
only the Desktop component ticked (the agent half is already installed on the
backend by the steps above). It finds `desktop/plugin.js` under that folder by
itself and installs it as `desktop-plugins/tailnet-services/`. Flip it on in
the same list afterwards if it arrives switched off.
