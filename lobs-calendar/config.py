"""Where the plugin's non-secret settings come from.

Config, not env vars: a calendar id is a behavioural setting, not a credential,
so it belongs in ``config.yaml`` alongside everything else the user tunes.

    # ~/.hermes/config.yaml
    lobs_calendar:
      calendar_id: abc123@group.calendar.google.com

Unset, it falls back to ``primary`` — the authenticated user's own calendar,
which is the only default that is correct on somebody else's machine. Every
tool and CLI command also takes an explicit calendar argument, which wins.
"""

import os
from pathlib import Path

FALLBACK_CALENDAR = "primary"
CONFIG_SECTION = "lobs_calendar"


def hermes_home():
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _load_section():
    """Read our section out of config.yaml. Absent or unreadable -> {}."""
    path = hermes_home() / "config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    section = loaded.get(CONFIG_SECTION)
    return section if isinstance(section, dict) else {}


def calendar_id(explicit=None):
    """Explicit argument > config.yaml > the user's own primary calendar."""
    if explicit:
        return explicit
    configured = _load_section().get("calendar_id")
    return configured if configured else FALLBACK_CALENDAR
