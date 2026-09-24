"""tailnet-services: a registry of web services on this machine, each reachable
from the tailnet.

The agent registers a service with `hermes tailnet-services add`. A service that
only listens on loopback gets a `tailscale serve` HTTPS front on the same port; a
service already listening on every interface is recorded as-is. The desktop app
reads the registry through dashboard/plugin_api.py and shows it on a Services page.

No model tools on purpose: the agent drives this through the CLI in a terminal,
which keeps the tool schema small and gives a human the same interface.
"""

import json

from . import registry, tailnet
from .registry import RegistryError
from .tailnet import TailnetError


def _add(args) -> int:
    name = registry.validate_name(args.name)
    port = args.port
    if not 1 <= port <= 65535:
        raise RegistryError(f"invalid port {port}")
    me = tailnet.self_status()
    if not me["dns_name"]:
        raise TailnetError("tailscale status has no Self.DNSName; is tailscale up?")

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

    existing = registry.get(name)
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

    entry = registry.upsert({
        "name": name,
        "port": port,
        "mode": mode,
        "serve_preexisting": serve_preexisting,
        "description": args.description or (existing or {}).get("description", ""),
        "repo": args.repo if args.repo is not None else (existing or {}).get("repo", ""),
    })
    url = tailnet.url_for(entry, me["dns_name"])
    verb = "updated" if existing else "registered"
    how = "tailscale serve (tailnet only)" if mode == "serve" else "direct, listens on all interfaces"
    print(f"{verb} {name}: {url}  [{how}]")
    return 0


def _rows(with_health: bool):
    services = registry.load()
    dns = tailnet.cached_self_status()["dns_name"]
    rows = []
    for s in services:
        row = dict(s, url=tailnet.url_for(s, dns), repo_url=registry.repo_url(s.get("repo", "")))
        if with_health:
            row["health"] = tailnet.probe(s["port"])
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
        print(f"{state:<5}{r['name']:<20} {r['url']:<48} {r.get('description', '')}")
        if r.get("repo"):
            print(f"{'':<25} repo: {r['repo']}")
    return 0


def _rm(args) -> int:
    entry = registry.get(args.name)
    if entry is None:
        print(f"no service named {args.name!r}")
        return 1
    if entry.get("mode") == "serve" and not entry.get("serve_preexisting") and not args.keep_serve:
        current = tailnet.serve_config().get(entry["port"])
        if current == tailnet.loopback_target(entry["port"]):
            tailnet.serve_off(entry["port"])
            print(f"tailscale serve on :{entry['port']} turned off")
        elif current:
            print(f"left the serve on :{entry['port']} alone: it proxies {current}, not this service")
    registry.remove(args.name)
    print(f"removed {args.name}")
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

    ls = sub.add_parser("ls", help="List registered services with health")
    ls.add_argument("--json", action="store_true")
    ls.add_argument("--no-probe", action="store_true", help="skip the health probe")

    rm = sub.add_parser("rm", help="Unregister a service and turn off its serve")
    rm.add_argument("name")
    rm.add_argument("--keep-serve", action="store_true")

    check = sub.add_parser("check", help="Health and serve state; exits 1 on any problem")
    check.add_argument("--json", action="store_true")


def _cli_handle(args) -> int:
    handlers = {"add": _add, "ls": _ls, "rm": _rm, "check": _check}
    try:
        return handlers[args.tsvc_cmd](args)
    except (RegistryError, TailnetError) as exc:
        print(f"error: {exc}")
        return 1


def register(ctx):
    ctx.register_cli_command(
        name="tailnet-services",
        help="Registry of web services on this machine, exposed on the tailnet",
        setup_fn=_cli_setup,
        handler_fn=_cli_handle,
    )
