"""A thin Google Calendar client over Hermes' own OAuth token.

Deliberately not a second credential system. The token is the one the
``google-workspace`` skill writes (``$HERMES_HOME/google_token.json``), so
setup, refresh and revocation all stay in one place and this plugin has no
secret of its own.

stdlib only. ``google-api-python-client`` is a dependency of the skill, not of
this plugin, and pulling it in here would make the plugin fail to import on a
machine where the skill was never set up.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.googleapis.com/calendar/v3"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class CalendarError(RuntimeError):
    """An API call failed, or the token is missing/unusable."""


def hermes_home():
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def token_path():
    return hermes_home() / "google_token.json"


class Calendar:
    """Just enough of the Calendar API for the things this plugin does."""

    def __init__(self, calendar_id, token_file=None):
        self.calendar_id = calendar_id
        self._token_file = Path(token_file) if token_file else token_path()
        self._access = None
        self._expires = 0.0

    # ---------------------------------------------------------------- auth
    def _stored(self):
        if not self._token_file.exists():
            raise CalendarError(
                f"no Google token at {self._token_file}. Set up the google-workspace "
                "skill first: python "
                "$HERMES_HOME/skills/productivity/google-workspace/scripts/setup.py --check"
            )
        try:
            return json.loads(self._token_file.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CalendarError(f"cannot read {self._token_file}: {exc}") from exc

    def access_token(self):
        """A live access token, refreshed from the stored refresh token.

        Cached in memory for the life of the process only. The refresh token is
        read on demand and never copied anywhere.
        """
        if self._access and time.time() < self._expires - 60:
            return self._access
        stored = self._stored()
        for field in ("refresh_token", "client_id", "client_secret"):
            if not stored.get(field):
                raise CalendarError(f"{self._token_file} has no {field}")
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": stored["refresh_token"],
            "client_id": stored["client_id"],
            "client_secret": stored["client_secret"],
        }).encode()
        req = urllib.request.Request(TOKEN_URI, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf8", "replace")
            raise CalendarError(f"token refresh failed ({exc.code}): {detail}") from exc
        self._access = payload["access_token"]
        self._expires = time.time() + float(payload.get("expires_in", 3600))
        return self._access

    # ----------------------------------------------------------------- http
    def call(self, method, path, params=None, body=None):
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.access_token())
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf8", "replace")
            raise CalendarError(f"{method} {path} -> {exc.code}: {detail}") from exc

    @property
    def _cal(self):
        return urllib.parse.quote(self.calendar_id, safe="")

    # ------------------------------------------------------------- events
    def list_events(self, time_min, time_max, private_property=None,
                    single_events=True, timezone="America/New_York"):
        """Every event in a window, following pagination to the end.

        ``single_events`` expands a recurrence into its instances. Pass False
        to reach the master event, which is what you patch to recolour a whole
        series at once.
        """
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": 250,
            "timeZone": timezone,
        }
        if single_events:
            params["singleEvents"] = "true"
            params["orderBy"] = "startTime"
        if private_property:
            params["privateExtendedProperty"] = private_property
        out, page = [], None
        while True:
            if page:
                params["pageToken"] = page
            batch = self.call("GET", f"/calendars/{self._cal}/events", params)
            out.extend(batch.get("items", []))
            page = batch.get("nextPageToken")
            if not page:
                return out

    def create(self, event):
        return self.call("POST", f"/calendars/{self._cal}/events", body=event)

    def patch(self, event_id, changes):
        return self.call("PATCH", f"/calendars/{self._cal}/events/{event_id}",
                         body=changes)

    def delete(self, event_id):
        self.call("DELETE", f"/calendars/{self._cal}/events/{event_id}")
        return {"status": "deleted", "id": event_id}

    def calendars(self):
        return self.call("GET", "/users/me/calendarList").get("items", [])

    def acl(self):
        """Sharing rules. Needs the full ``calendar`` scope, not ``calendar.events``.

        Used to explain the commonest colour complaint: per-event colours only
        render for accounts with write access.
        """
        return self.call("GET", f"/calendars/{self._cal}/acl").get("items", [])

    def share(self, email, role="writer"):
        """Grant a person a role on this calendar.

        ``writer`` is the level at which Google starts rendering per-event
        colours for someone; a ``reader`` sees the whole calendar in one flat
        colour however each event is coloured. Idempotent — PUT on the rule id
        creates or updates.
        """
        rule_id = urllib.parse.quote(f"user:{email}", safe="")
        return self.call(
            "PUT", f"/calendars/{self._cal}/acl/{rule_id}",
            body={"role": role, "scope": {"type": "user", "value": email}})
