"""Tailscale and socket helpers shared by the CLI and the dashboard API.

Everything shells out to the tailscale CLI. The tailnet DNS name is read at
runtime from `tailscale status --json` and never stored in code.
"""

import http.client
import json
import shutil
import subprocess
import time
from pathlib import Path

_CANDIDATES = ("/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
               "/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/bin/tailscale")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_WILDCARD = {"*", "0.0.0.0", "::"}


class TailnetError(Exception):
    pass


def tailscale_bin() -> str:
    # launchd jobs often run with a thin PATH, so fall back to the usual install spots.
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in _CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise TailnetError("tailscale CLI not found")


def _run(args, timeout=15):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TailnetError(f"{' '.join(args)}: {exc}") from exc
    return proc


def _tailscale(*args, timeout=15):
    proc = _run([tailscale_bin(), *args], timeout=timeout)
    if proc.returncode != 0:
        raise TailnetError((proc.stderr or proc.stdout).strip()
                           or f"tailscale {' '.join(args)} exited {proc.returncode}")
    return proc.stdout


def self_status() -> dict:
    data = json.loads(_tailscale("status", "--json"))
    me = data.get("Self") or {}
    return {
        "dns_name": (me.get("DNSName") or "").rstrip("."),
        "ips": list(me.get("TailscaleIPs") or []),
    }


_status_cache = {"at": 0.0, "value": None}


def cached_self_status(ttl=60.0) -> dict:
    # The dashboard polls every few seconds; the DNS name changes about never.
    now = time.monotonic()
    if _status_cache["value"] is None or now - _status_cache["at"] > ttl:
        _status_cache["value"] = self_status()
        _status_cache["at"] = now
    return _status_cache["value"]


def listen_hosts(port: int) -> list:
    """Hosts a TCP listener on *port* is bound to, e.g. ['127.0.0.1'] or ['*']."""
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    proc = _run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fn"], timeout=10)
    hosts = []
    for line in proc.stdout.splitlines():
        if not line.startswith("n"):
            continue
        addr = line[1:]
        host = addr.rsplit(":", 1)[0].strip("[]")
        if host not in hosts:
            hosts.append(host)
    return hosts


def classify(port: int, tailnet_ips=()) -> str:
    """'none', 'loopback' (needs a serve), 'direct' (tailnet can reach it as is),
    or 'other' (bound somewhere the tailnet can't reach)."""
    hosts = listen_hosts(port)
    if not hosts:
        return "none"
    if any(h in _WILDCARD or h in tailnet_ips for h in hosts):
        return "direct"
    if all(h in _LOOPBACK or h.startswith("127.") for h in hosts):
        return "loopback"
    return "other"


def serve_config() -> dict:
    """{port: proxy target} for every HTTPS `tailscale serve` handler on '/'."""
    raw = _tailscale("serve", "status", "--json").strip()
    data = json.loads(raw) if raw else {}
    out = {}
    for hostport, web in (data.get("Web") or {}).items():
        port = hostport.rsplit(":", 1)[-1]
        if not port.isdigit():
            continue
        handler = ((web or {}).get("Handlers") or {}).get("/") or {}
        out[int(port)] = handler.get("Proxy") or handler.get("Path") or handler.get("Text") or "?"
    for port, spec in (data.get("TCP") or {}).items():
        if str(port).isdigit() and int(port) not in out and not (spec or {}).get("HTTPS"):
            out[int(port)] = "tcp-forward"
    return out


def loopback_target(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def serve_on(port: int) -> None:
    _tailscale("serve", "--bg", f"--https={port}", loopback_target(port), timeout=30)


def serve_off(port: int) -> None:
    _tailscale("serve", f"--https={port}", "off", timeout=30)


def url_for(entry: dict, dns_name: str) -> str:
    scheme = "https" if entry.get("mode") == "serve" else "http"
    return f"{scheme}://{dns_name}:{entry['port']}/"


def probe(port: int, timeout: float = 2.0) -> dict:
    """GET http://127.0.0.1:<port>/. Any HTTP response, 404 and 500 included,
    means something is serving; only a connect or read failure is 'down'."""
    started = time.monotonic()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/", headers={"User-Agent": "hermes-tailnet-services/1"})
        status = conn.getresponse().status
        return {"up": True, "status": status,
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"up": False, "status": None, "error": type(exc).__name__}
    finally:
        conn.close()
