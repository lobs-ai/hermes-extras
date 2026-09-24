"""tailnet-services: a registry of web services on this machine, each reachable
from the tailnet.

The agent registers a service with `hermes tailnet-services add`. A service that
only listens on loopback gets a `tailscale serve` HTTPS front on the same port; a
service already listening on every interface is recorded as-is. With --start the
service also gets its own launchd LaunchAgent (launchd.py), so it starts at boot
and comes back after a crash. The desktop app reads the registry through
dashboard/plugin_api.py and shows it on a Services page.

No model tools on purpose: the agent drives this through the CLI in a terminal,
which keeps the tool schema small and gives a human the same interface.
"""

import json
import os
import re
import time
from pathlib import Path

from . import launchd, registry, tailnet
from .launchd import LaunchdError
from .registry import RegistryError
from .tailnet import TailnetError

PORT_WAIT_SECONDS = 30
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env(pairs) -> dict:
    env = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not ENV_KEY_RE.fullmatch(key):
            raise RegistryError(f"--env wants KEY=VALUE, got {pair!r}")
        env[key] = value
    return env


def supervision(args, existing) -> dict:
    """start_cmd/cwd/env for the new entry: {} for unmanaged.

    --start sets or replaces it, --no-start drops it, and neither carries the
    previous values forward (like --description and --repo), so re-running
    `add` to change the port doesn't silently unmanage a service.
    """
    existing = existing or {}
    if args.no_start:
        if args.start or args.cwd or args.env:
            raise RegistryError("--no-start can't be combined with --start, --cwd or --env")
        return {}
    if args.start is not None:
        if not args.start.strip():
            raise RegistryError("--start is empty")
        cwd = Path(os.path.expanduser(args.cwd or os.getcwd())).resolve()
        if not cwd.is_dir():
            raise RegistryError(f"--cwd {cwd} is not a directory")
        env = parse_env(args.env) if args.env else dict(existing.get("env") or {})
        return {"start_cmd": args.start.strip(), "cwd": str(cwd), "env": env}
    if existing.get("start_cmd"):
        if args.cwd or args.env:
            cwd = Path(os.path.expanduser(args.cwd)).resolve() if args.cwd else Path(existing["cwd"])
            if not cwd.is_dir():
                raise RegistryError(f"--cwd {cwd} is not a directory")
            env = parse_env(args.env) if args.env else dict(existing.get("env") or {})
            return {"start_cmd": existing["start_cmd"], "cwd": str(cwd), "env": env}
        return {k: existing[k] for k in ("start_cmd", "cwd", "env") if k in existing}
    if args.cwd or args.env:
        raise RegistryError("--cwd and --env only apply with --start")
    return {}


def wait_for_port(port: int, tailnet_ips=(), timeout: float = PORT_WAIT_SECONDS) -> str:
    """Poll the binding until something listens or *timeout* passes."""
    deadline = time.monotonic() + timeout
    while True:
        binding = tailnet.classify(port, tailnet_ips)
        if binding != "none" or time.monotonic() >= deadline:
            return binding
        time.sleep(0.5)


def _print_log_tail(name: str, lines: int = 20) -> None:
    tail = launchd.tail_log(name, lines)
    print(f"last {len(tail)} line(s) of {launchd.log_path(name)}:")
    for line in tail:
        print(f"  {line}")


def _add(args) -> int:
    name = registry.validate_name(args.name)
    port = args.port
    if not 1 <= port <= 65535:
        raise RegistryError(f"invalid port {port}")
    reserved = registry.reserved_ports()
    if port in reserved:
        raise RegistryError(f"port {port} is reserved in config.yaml: {reserved[port]}")
    existing = registry.get(name)
    managed = supervision(args, existing)
    me = tailnet.self_status()
    if not me["dns_name"]:
        raise TailnetError("tailscale status has no Self.DNSName; is tailscale up?")

    was_managed = bool((existing or {}).get("start_cmd"))
    if managed:
        clash = next((s for s in registry.load() if s["port"] == port and s["name"] != name), None)
        if clash:
            raise RegistryError(f"port {port} is already registered as '{clash['name']}'")
        ours = was_managed and existing["port"] == port
        if not ours and tailnet.classify(port, me["ips"]) != "none":
            raise RegistryError(
                f"something is already listening on port {port}; stop it before handing the "
                "port to launchd, or register it without --start")
        if launchd.load(dict(managed, name=name)):
            print(f"launchd job {launchd.label(name)} loaded ({launchd.plist_path(name)})")
        else:
            print(f"launchd job {launchd.label(name)} already loaded with this command; left running")
        binding = wait_for_port(port, me["ips"])
        if binding == "none":
            print(f"error: nothing listened on port {port} within {PORT_WAIT_SECONDS}s. "
                  f"The job stays loaded; fix the command and re-run add, or `rm {name}`.")
            _print_log_tail(name)
            return 1
    else:
        if was_managed:
            launchd.unload(name)
            print(f"launchd job {launchd.label(name)} removed; {name} is no longer supervised")
        binding = tailnet.classify(port, me["ips"])
    mode = args.mode
    if mode == "auto":
        if binding == "none":
            raise RegistryError(
                f"nothing is listening on port {port}. Start the service first, or pass "
                "--mode serve (it will listen on loopback) or --mode direct (all interfaces).")
        if binding == "other":
            raise RegistryError(
                f"port {port} listens only on {', '.join(tailnet.listen_hosts(port))}, which the "
                "tailnet can't reach. Bind it to 127.0.0.1 or 0.0.0.0.")
        mode = "serve" if binding == "loopback" else "direct"
    elif mode == "serve" and binding == "direct":
        # A serve on a port the service already owns on every interface collides with it.
        raise RegistryError(
            f"port {port} already listens on all interfaces; register it with --mode direct")

    serve_preexisting = False
    if mode == "serve":
        current = tailnet.serve_config().get(port)
        target = tailnet.loopback_target(port)
        if current and current != target:
            raise RegistryError(f"port {port} already has a tailscale serve for {current}")
        if current == target:
            # Someone set this serve up before us. Keep the flag from the first
            # registration so `rm` never turns off a serve it didn't create.
            serve_preexisting = (existing or {}).get("serve_preexisting", True)
        else:
            tailnet.serve_on(port)
    if existing and existing.get("mode") == "serve" and existing["port"] != port \
            and not existing.get("serve_preexisting"):
        tailnet.serve_off(existing["port"])

    entry = registry.upsert(dict({
        "name": name,
        "port": port,
        "mode": mode,
        "serve_preexisting": serve_preexisting,
        "description": args.description or (existing or {}).get("description", ""),
        "repo": args.repo if args.repo is not None else (existing or {}).get("repo", ""),
    }, **managed))
    url = tailnet.url_for(entry, me["dns_name"])
    verb = "updated" if existing else "registered"
    how = "tailscale serve (tailnet only)" if mode == "serve" else "direct, listens on all interfaces"
    if managed:
        how += "; launchd starts it at boot and after a crash"
    print(f"{verb} {name}: {url}  [{how}]")
    return 0


def _rows(with_health: bool):
    services = registry.load()
    dns = tailnet.cached_self_status()["dns_name"]
    rows = []
    for s in services:
        row = dict(s, url=tailnet.url_for(s, dns), repo_url=registry.repo_url(s.get("repo", "")),
                   managed=bool(s.get("start_cmd")))
        if with_health:
            row["health"] = tailnet.probe(s["port"])
            if row["managed"]:
                row["launchd"] = launchd.status(s["name"])
        rows.append(row)
    return rows


def _ls(args) -> int:
    rows = _rows(with_health=not args.no_probe)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No services registered. Add one with: hermes tailnet-services add NAME --port N")
        return 0
    for r in rows:
        state = "" if args.no_probe else ("up  " if r["health"]["up"] else "DOWN")
        managed = "launchd" if r["managed"] else ""
        print(f"{state:<5}{r['name']:<20} {managed:<8} {r['url']:<48} {r.get('description', '')}")
        if r.get("repo"):
            print(f"{'':<34} repo: {r['repo']}")
        if r["managed"]:
            job = r.get("launchd") or {}
            pid = f", pid {job['pid']}" if job.get("pid") else ""
            where = f"{job.get('state') or 'not loaded'}{pid}" if not args.no_probe else ""
            print(f"{'':<34} start: {r['start_cmd']}" + (f"  [{where}]" if where else ""))
    return 0


def _rm(args) -> int:
    entry = registry.get(args.name)
    if entry is None:
        # An `add --start` whose service never listened leaves its job loaded
        # but nothing registered; rm still has to be able to clean that up.
        if registry.NAME_RE.fullmatch(args.name or "") and launchd.unload(args.name):
            print(f"launchd job {launchd.label(args.name)} stopped and its plist deleted "
                  "(it was never registered)")
            return 0
        print(f"no service named {args.name!r}")
        return 1
    if entry.get("mode") == "serve" and not entry.get("serve_preexisting") and not args.keep_serve:
        current = tailnet.serve_config().get(entry["port"])
        if current == tailnet.loopback_target(entry["port"]):
            tailnet.serve_off(entry["port"])
            print(f"tailscale serve on :{entry['port']} turned off")
        elif current:
            print(f"left the serve on :{entry['port']} alone: it proxies {current}, not this service")
    if entry.get("start_cmd") or launchd.plist_path(args.name).exists():
        if launchd.unload(args.name):
            print(f"launchd job {launchd.label(args.name)} stopped and its plist deleted")
    registry.remove(args.name)
    print(f"removed {args.name}")
    return 0


def _managed_entry(name: str) -> dict:
    entry = registry.get(name)
    if entry is None:
        raise RegistryError(f"no service named {name!r}")
    if not entry.get("start_cmd"):
        raise RegistryError(f"{name} has no start command, so there is nothing to restart. "
                            "Register it with --start first.")
    return entry


def _restart(args) -> int:
    entry = _managed_entry(args.name)
    if not launchd.status(entry["name"])["loaded"]:
        # Plist deleted or the job booted out by hand: put it back rather than fail.
        launchd.load(entry)
        print(f"launchd job {launchd.label(entry['name'])} was not loaded; loaded it")
    else:
        before = launchd.status(entry["name"])["pid"]
        launchd.restart(entry["name"])
        print(f"kickstarted {launchd.label(entry['name'])} (old pid {before or '-'})")
    ips = tailnet.cached_self_status()["ips"]
    # kickstart -k returns before the old process has let go of the port.
    time.sleep(1)
    if wait_for_port(entry["port"], ips) == "none":
        print(f"error: nothing listened on port {entry['port']} within {PORT_WAIT_SECONDS}s")
        _print_log_tail(entry["name"])
        return 1
    job = launchd.status(entry["name"])
    print(f"{entry['name']} is {job['state']} with pid {job['pid']}, listening on :{entry['port']}")
    return 0


def _logs(args) -> int:
    entry = _managed_entry(args.name)
    path = launchd.log_path(entry["name"])
    if not path.exists():
        print(f"no log yet at {path}")
        return 0
    for line in launchd.tail_log(entry["name"], args.lines):
        print(line)
    return 0


def _check(args) -> int:
    serves = tailnet.serve_config()
    rows = _rows(with_health=True)
    problems = 0
    for r in rows:
        issues = []
        if not r["health"]["up"]:
            issues.append("not answering on 127.0.0.1")
        if r["mode"] == "serve" and serves.get(r["port"]) != tailnet.loopback_target(r["port"]):
            issues.append("tailscale serve missing; re-run `add` to restore it")
        if r["mode"] == "direct" and tailnet.classify(r["port"]) == "loopback":
            issues.append("now listens on loopback only; re-run `add` to serve it")
        if r["managed"]:
            job = r.get("launchd") or {}
            if not job.get("loaded"):
                issues.append(f"launchd job not loaded; run `restart {r['name']}`")
            elif job.get("state") != "running":
                issues.append(f"launchd job is {job.get('state')} (last exit {job.get('last_exit')}); "
                              f"see `logs {r['name']}`")
        problems += bool(issues)
        r["issues"] = issues
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            mark = "ok  " if not r["issues"] else "FAIL"
            print(f"{mark} {r['name']:<20} {r['url']}" + ("" if not r["issues"]
                                                             else "  <- " + "; ".join(r["issues"])))
        if not rows:
            print("No services registered.")
    return 1 if problems else 0


def _cli_setup(parser):
    sub = parser.add_subparsers(dest="tsvc_cmd", required=True)

    add = sub.add_parser("add", help="Register a service (re-running updates it)")
    add.add_argument("name", help="short id: lowercase letters, digits, dashes")
    add.add_argument("--port", type=int, required=True)
    add.add_argument("--description", "-d", default="")
    add.add_argument("--repo", default=None, help="owner/repo, a URL, or a local path")
    add.add_argument("--mode", choices=["auto", "serve", "direct"], default="auto",
                     help="auto: serve if it listens on loopback, direct if on all interfaces")
    add.add_argument("--start", default=None, metavar="CMD",
                     help="shell command that runs the service in the foreground; launchd "
                          "starts it now, at boot, and after a crash")
    add.add_argument("--cwd", default=None, help="working directory for --start (default: here)")
    add.add_argument("--env", action="append", default=[], metavar="KEY=VAL",
                     help="environment variable for --start (repeatable)")
    add.add_argument("--no-start", action="store_true",
                     help="stop supervising: unload the launchd job and delete its plist")

    ls = sub.add_parser("ls", help="List registered services with health")
    ls.add_argument("--json", action="store_true")
    ls.add_argument("--no-probe", action="store_true", help="skip the health probe")

    rm = sub.add_parser("rm", help="Unregister a service and turn off its serve")
    rm.add_argument("name")
    rm.add_argument("--keep-serve", action="store_true")

    check = sub.add_parser("check", help="Health and serve state; exits 1 on any problem")
    check.add_argument("--json", action="store_true")

    restart = sub.add_parser("restart", help="Restart a --start service through launchd")
    restart.add_argument("name")

    logs = sub.add_parser("logs", help="Tail the log of a --start service")
    logs.add_argument("name")
    logs.add_argument("-n", "--lines", type=int, default=50)


def _cli_handle(args) -> int:
    handlers = {"add": _add, "ls": _ls, "rm": _rm, "check": _check,
                "restart": _restart, "logs": _logs}
    try:
        return handlers[args.tsvc_cmd](args)
    except (RegistryError, TailnetError, LaunchdError) as exc:
        print(f"error: {exc}")
        return 1


def register(ctx):
    ctx.register_cli_command(
        name="tailnet-services",
        help="Registry of web services on this machine, exposed on the tailnet",
        setup_fn=_cli_setup,
        handler_fn=_cli_handle,
    )
