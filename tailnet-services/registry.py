"""The service registry: one JSON file under the Hermes home.

Only services the agent registers explicitly live here. Nothing is discovered,
so an entry means someone decided the service is worth remembering.
"""

import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
from pathlib import Path

PLUGIN = "tailnet-services"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class RegistryError(Exception):
    pass


def data_dir() -> Path:
    # Resolved per call so a profile switch lands in that profile's home.
    from hermes_constants import get_hermes_home
    root = get_hermes_home() / "plugin-data" / PLUGIN
    root.mkdir(parents=True, exist_ok=True)
    return root


def registry_path() -> Path:
    return data_dir() / "services.json"


@contextlib.contextmanager
def _locked():
    # The CLI can run from several agent turns at once; serialise read-modify-write
    # so two concurrent `add`s can't drop each other's entry.
    lock_path = data_dir() / ".lock"
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load() -> list:
    path = registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"cannot read {path}: {exc}") from exc
    services = data.get("services", []) if isinstance(data, dict) else []
    return [s for s in services if isinstance(s, dict) and s.get("name")]


def _write(services: list) -> None:
    path = registry_path()
    payload = json.dumps({"version": 1, "services": services}, indent=2) + "\n"
    # Temp file in the same directory, then rename: a reader (the dashboard API)
    # sees the old file or the new one, never half of one.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".services.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def repo_url(repo: str):
    """A browsable link for the repo field, or None for a local path."""
    if not repo:
        return None
    if repo.startswith(("https://", "http://")):
        return repo
    parts = repo.strip("/").split("/")
    # owner/repo shorthand is the common case; a local path has no link.
    if len(parts) == 2 and all(parts) and not repo.startswith(("~", ".", "/")):
        return f"https://github.com/{repo.strip('/')}"
    return None


def get(name: str):
    return next((s for s in load() if s["name"] == name), None)


def reserved_ports() -> dict:
    """{port: reason} from config.yaml, ports `add` must refuse outright:

        tailnet_services:
          reserved_ports:
            8000: "live game server; never touch"

    Kept in config, not code: which ports are off limits is a fact about one
    machine, not about the plugin.
    """
    from hermes_constants import get_hermes_home
    path = get_hermes_home() / "config.yaml"
    try:
        import yaml
        section = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("tailnet_services") or {}
    except Exception:
        return {}
    out = {}
    for port, reason in (section.get("reserved_ports") or {}).items():
        try:
            out[int(port)] = str(reason or "reserved")
        except (TypeError, ValueError):
            continue
    return out


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name or ""):
        raise RegistryError(
            f"invalid name {name!r}: use lowercase letters, digits and dashes (max 48)")
    return name


def upsert(entry: dict) -> dict:
    """Insert or replace by name. A port may belong to one entry only."""
    with _locked():
        services = load()
        clash = next((s for s in services
                      if s["port"] == entry["port"] and s["name"] != entry["name"]), None)
        if clash:
            raise RegistryError(
                f"port {entry['port']} is already registered as '{clash['name']}'")
        previous = next((s for s in services if s["name"] == entry["name"]), None)
        entry = dict(entry)
        entry["added_at"] = (previous or {}).get("added_at") or int(time.time())
        entry["updated_at"] = int(time.time())
        services = [s for s in services if s["name"] != entry["name"]] + [entry]
        services.sort(key=lambda s: s["name"])
        _write(services)
        return entry


def remove(name: str):
    with _locked():
        services = load()
        found = next((s for s in services if s["name"] == name), None)
        if found is None:
            return None
        _write([s for s in services if s["name"] != name])
        return found
