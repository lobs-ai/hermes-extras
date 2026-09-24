"""Backend for the desktop Services page, mounted at /api/plugins/tailnet-services/.

The dashboard imports this file on its own (not as part of the plugin package),
so the sibling modules are loaded by path rather than by relative import.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter

_PKG_DIR = Path(__file__).resolve().parent.parent


def _load(mod: str):
    name = f"hermes_tailnet_services_{mod}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _PKG_DIR / f"{mod}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {mod}.py from {_PKG_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry = _load("registry")
tailnet = _load("tailnet")
launchd = _load("launchd")

router = APIRouter()


@router.get("/services")
async def services():
    try:
        entries = await asyncio.to_thread(registry.load)
    except registry.RegistryError as exc:
        return {"services": [], "error": str(exc)}
    try:
        me = await asyncio.to_thread(tailnet.cached_self_status)
        dns, err = me["dns_name"], None
    except Exception as exc:
        # Without the DNS name there is no URL to show, but the list and health still are.
        dns, err = "", f"tailscale status failed: {exc}"

    # Probes run in parallel so one hung service costs ~2s total, not 2s each.
    managed = [e for e in entries if e.get("start_cmd")]
    healths, jobs = await asyncio.gather(
        asyncio.gather(*(asyncio.to_thread(tailnet.probe, e["port"], 2.0) for e in entries)),
        asyncio.gather(*(asyncio.to_thread(_job, e["name"]) for e in managed)))
    job_by_name = {e["name"]: job for e, job in zip(managed, jobs)}

    rows = []
    for entry, health in zip(entries, healths):
        row = {
            "name": entry["name"],
            "port": entry["port"],
            "mode": entry.get("mode", "serve"),
            "description": entry.get("description", ""),
            "repo": entry.get("repo", ""),
            "repo_url": registry.repo_url(entry.get("repo", "")),
            "url": tailnet.url_for(entry, dns) if dns else None,
            "health": health,
            "managed": bool(entry.get("start_cmd")),
            "start_cmd": entry.get("start_cmd"),
        }
        if row["managed"]:
            row["launchd"] = job_by_name.get(entry["name"])
        rows.append(row)
    return {"services": rows, "host": dns or None, "error": err}


def _job(name: str) -> dict:
    try:
        job = launchd.status(name)
    except Exception as exc:
        return {"state": None, "pid": None, "error": str(exc)}
    return {"state": job["state"] if job["loaded"] else "not loaded", "pid": job["pid"]}
