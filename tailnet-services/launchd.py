"""Per-service launchd LaunchAgents for services registered with a start command.

Each managed service gets its own job, ai.hermes.tailnet-services.<name>, rather
than running as a child of the Hermes gateway: a gateway restart (update, config
change) would otherwise bounce every service, and launchd already gives start at
boot and restart on crash for free.
"""

import contextlib
import os
import plistlib
import re
import subprocess
import tempfile
import time
from pathlib import Path

LABEL_PREFIX = "ai.hermes.tailnet-services."
THROTTLE_SECONDS = 10


class LaunchdError(Exception):
    pass


def label(name: str) -> str:
    return LABEL_PREFIX + name


def plist_path(name: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label(name)}.plist"


def log_dir() -> Path:
    from hermes_constants import get_hermes_home
    path = get_hermes_home() / "plugin-data" / "tailnet-services" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(name: str) -> Path:
    return log_dir() / f"{name}.log"


def plist_dict(entry: dict, log_file) -> dict:
    """The LaunchAgent for a registry entry. Pure, so it can be tested."""
    # A login shell picks up the Homebrew PATH that launchd's thin environment
    # lacks; `exec` replaces the shell so launchd supervises the real process.
    job = {
        "Label": label(entry["name"]),
        "ProgramArguments": ["/bin/zsh", "-lc", "exec " + entry["start_cmd"]],
        "WorkingDirectory": entry["cwd"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    }
    if entry.get("env"):
        job["EnvironmentVariables"] = {str(k): str(v) for k, v in entry["env"].items()}
    return job


def write_plist(entry: dict) -> Path:
    path = plist_path(entry["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(plist_dict(entry, log_path(entry["name"])))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{label(entry['name'])}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args, timeout=20):
    try:
        return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchdError(f"launchctl {' '.join(args)}: {exc}") from exc


def _bootout(name: str) -> bool:
    """Unload the job if loaded. True if something was unloaded."""
    proc = _launchctl("bootout", f"{_domain()}/{label(name)}")
    # A job that isn't loaded fails with a code that varies by macOS release, so
    # judge by whether the job is gone afterwards rather than by the code.
    if proc.returncode == 0:
        return True
    if status(name)["loaded"]:
        raise LaunchdError(f"launchctl bootout {label(name)} failed: "
                           f"{(proc.stderr or proc.stdout).strip()}")
    return False


def load(entry: dict) -> bool:
    """Write the plist and (re)load the job so it starts now and at every login.

    Returns False when the job is already loaded from an identical plist, so
    re-running `add` to change a description doesn't bounce the service.
    """
    path = plist_path(entry["name"])
    wanted = plist_dict(entry, log_path(entry["name"]))
    try:
        current = plistlib.loads(path.read_bytes()) if path.exists() else None
    except Exception:
        current = None
    if current == wanted and status(entry["name"])["loaded"]:
        return False
    write_plist(entry)
    _bootout(entry["name"])
    for attempt in range(5):
        proc = _launchctl("bootstrap", _domain(), str(path))
        if proc.returncode == 0:
            break
        # Right after a bootout launchd can still be tearing the old job down and
        # answers "Input/output error"; it clears within a second or two.
        time.sleep(1)
    if proc.returncode != 0:
        raise LaunchdError(f"launchctl bootstrap {path} failed: {(proc.stderr or proc.stdout).strip()}")
    return True


def unload(name: str) -> bool:
    """Stop the job and delete its plist. True if either existed."""
    had_job = _bootout(name)
    path = plist_path(name)
    had_plist = path.exists()
    if had_plist:
        path.unlink()
    return had_job or had_plist


def restart(name: str) -> None:
    proc = _launchctl("kickstart", "-k", f"{_domain()}/{label(name)}")
    if proc.returncode != 0:
        raise LaunchdError(f"launchctl kickstart {label(name)} failed: "
                           f"{(proc.stderr or proc.stdout).strip() or 'job not loaded'}")


_STATE_RE = re.compile(r"^\s*state = (\S+)", re.M)
_PID_RE = re.compile(r"^\s*pid = (\d+)", re.M)
_EXIT_RE = re.compile(r"^\s*last exit code = (.+)$", re.M)


def parse_print(text: str) -> dict:
    state = _STATE_RE.search(text)
    pid = _PID_RE.search(text)
    last_exit = _EXIT_RE.search(text)
    return {
        "state": state.group(1) if state else None,
        "pid": int(pid.group(1)) if pid else None,
        "last_exit": last_exit.group(1).strip() if last_exit else None,
    }


def status(name: str) -> dict:
    """{loaded, state, pid, last_exit} from `launchctl print`."""
    proc = _launchctl("print", f"{_domain()}/{label(name)}", timeout=10)
    if proc.returncode != 0:
        return {"loaded": False, "state": None, "pid": None, "last_exit": None}
    return dict(parse_print(proc.stdout), loaded=True)


def tail_log(name: str, lines: int = 20) -> list:
    path = log_path(name)
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        # Services can log a lot; read only the end.
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - max(lines, 1) * 400))
        data = fh.read().decode("utf-8", errors="replace")
    return data.splitlines()[-lines:] if lines > 0 else []
