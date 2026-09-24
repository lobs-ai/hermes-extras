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
  launchd.py              # per-service LaunchAgent for `add --start`
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
source repo and an Open link.

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

### Keeping a service running (`--start`)

A service the agent starts itself can be handed to launchd, so it comes up at
boot and restarts after a crash:

```sh
hermes tailnet-services add notes --port 8080 -d "Notes app" \
  --start "npm run serve -- --port 8080" --cwd ~/src/notes --env NODE_ENV=production
hermes tailnet-services restart notes     # launchctl kickstart -k, then waits for the port
hermes tailnet-services logs notes -n 100 # tail its stdout/stderr
hermes tailnet-services add notes --port 8080 --no-start   # stop supervising, keep the entry
hermes tailnet-services rm notes          # also unloads the job and deletes its plist
```

Each service gets its own LaunchAgent,
`~/Library/LaunchAgents/ai.hermes.tailnet-services.<name>.plist`, rather than
running as a child of the Hermes gateway, so a gateway restart doesn't bounce
every service. The job runs `/bin/zsh -lc "exec <command>"` (a login shell for
the Homebrew `PATH`; `exec` so launchd supervises the real process) in `--cwd`,
which defaults to the directory you ran `add` from, with `KeepAlive`,
`RunAtLoad` and a 10 second `ThrottleInterval`. Output goes to
`$HERMES_HOME/plugin-data/tailnet-services/logs/<name>.log`.

The command must stay in the foreground and listen on `--port`. `add` loads
the job, waits up to 30 seconds for the port, and only then sets up the serve.
If nothing listens, it prints the end of the log and exits 1 without
registering. The job stays loaded so you can read its log; fix the command and
re-run `add`, or `rm` the name. Re-running `add` without `--start` keeps the
existing command, the same way `-d` and `--repo` carry forward. `--start` also
refuses a port something else is already listening on.

`ls` marks supervised services `launchd` and shows the command and pid.
`check` fails when a supervised job is not loaded or not running.

### Reserved ports

Ports that must never be registered or exposed live in `config.yaml`, and
`add` refuses them outright with the reason:

```yaml
tailnet_services:
  reserved_ports:
    8000: "production game server; never touch"
    9000: "must stay loopback-only"
```

### Health

The desktop page calls `GET /api/plugins/tailnet-services/services`, which
probes `http://127.0.0.1:<port>/` on the backend machine with a 2 second
timeout. Any HTTP response, a 404 or a 500 included, counts as up: the dot
answers "is something listening", not "is it healthy". The page refetches
every 15 seconds.

Open, and the URL itself, open the service in the desktop app's built-in
Browser pane on the right. The plugin SDK has no call for that pane, so both
are rendered as markdown links through the SDK's `MessageTextContent`, which
uses the app's own link component: a click opens the in-app Browser,
⌘-click or middle-click opens the system browser, and right-click gives the
app's link menu. A small button next to Open always uses the system browser
(`ctx.os.openExternal`). Supervised services show a "starts at boot" label
whose tooltip gives the command and its launchd state.

### Install

Install it from the desktop app: **Capabilities → Plugins → Install from Git**,
identifier `lobs-ai/hermes-extras/tailnet-services`. When the app is connected
to a remote backend, that installs both halves: the agent half lands on the
backend as `~/.hermes/plugins/tailnet-services/`, and the desktop half
(`desktop/plugin.js`) as `desktop-plugins/tailnet-services/`. From a shell on
the backend the equivalent is:

```sh
hermes plugins install lobs-ai/hermes-extras#tailnet-services
hermes plugins enable tailnet-services
hermes plugins update tailnet-services     # later, to pick up changes
```

Then restart the dashboard backend once. Plugin API routes mount only at
startup, and the Services page reads `/api/plugins/tailnet-services/services`.

Don't also enable it as `hermes-extras/tailnet-services` from a whole-repo
install of this monorepo: both copies register the same `hermes
tailnet-services` command, and only the standalone copy puts `dashboard/`
where the dashboard looks for it.
