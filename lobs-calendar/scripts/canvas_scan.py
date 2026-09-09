#!/usr/bin/env python3
"""Scan the bot mailbox for forwarded Canvas mail and print date candidates.

Deliberately dumb. It finds messages, pulls plain text, and regexes out anything
that looks like a due date. It does NOT decide what is real — a cron agent reads
this output and reasons about it, because "P3 released, due in two weeks" is a
sentence a regex will get wrong and a model will not.

Stdlib only, and it borrows the lobs-calendar plugin's OAuth token rather than
holding a credential of its own.

Watermark lives at ~/.hermes/course-deadlines/.canvas-watermark so each run only
reports mail that arrived since the last one. Pass --since-days N to override,
--all to ignore the watermark entirely, --no-commit to leave it unchanged.
"""

import argparse
import base64
import datetime as dt
import importlib.util
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

HOME = pathlib.Path(os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes"))
PLUGIN = HOME / "plugins" / "hermes-extras" / "lobs-calendar" / "__init__.py"
STATE = HOME / "course-deadlines" / ".canvas-watermark"

# Canvas notifications arrive from Instructure, but a UMich instance may relay
# through its own domain, and Rafe forwards from rsymonds@umich.edu — so match
# broadly and let the agent discard what is not a course announcement.
QUERY = (
    "(from:instructure.com OR from:canvas OR subject:canvas "
    'OR "umich.instructure.com" OR from:umich.edu) '
    "-subject:(forwarding confirmation)"
)

# Dates worth surfacing. Deliberately generous; precision is the agent's job.
DATE_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?",
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    r"\b(?:due|deadline|closes?|opens?|released?)\b[^.\n]{0,60}",
]
DATE_RE = re.compile("|".join(DATE_PATTERNS), re.I)


def token():
    spec = importlib.util.spec_from_file_location("lobs_calendar", PLUGIN)
    lc = importlib.util.module_from_spec(spec)
    sys.modules["lobs_calendar"] = lc
    spec.loader.exec_module(lc)
    from lobs_calendar.google import Calendar

    return Calendar(lc.calendar_id()).access_token()


def gmail(tok, path, params=None):
    url = "https://gmail.googleapis.com/gmail/v1/users/me/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def plain_text(payload):
    """Prefer text/plain; fall back to stripping the HTML part."""
    plain, html = [], []
    def walk(p):
        mime = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if data:
            try:
                txt = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
            except Exception:
                txt = ""
            (plain if mime == "text/plain" else html).append(txt)
        for part in p.get("parts") or []:
            walk(part)
    walk(payload)
    text = "\n".join(plain) or "\n".join(html)
    text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-days", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--max", type=int, default=25)
    args = ap.parse_args()

    q = QUERY
    if args.all:
        pass
    elif args.since_days:
        q += f" newer_than:{args.since_days}d"
    elif STATE.exists():
        q += f" after:{STATE.read_text().strip()}"
    else:
        q += " newer_than:7d"

    tok = token()
    res = gmail(tok, "messages", {"q": q, "maxResults": args.max})
    ids = [m["id"] for m in res.get("messages", [])]

    out = []
    for mid in ids:
        full = gmail(tok, f"messages/{mid}", {"format": "full"})
        hs = {h["name"]: h["value"] for h in full["payload"].get("headers", [])}
        body = plain_text(full["payload"])
        hits = sorted({" ".join(m.group(0).split()) for m in DATE_RE.finditer(body)})
        out.append({
            "id": mid,
            "date": hs.get("Date"),
            "from": hs.get("From"),
            "subject": hs.get("Subject"),
            "date_candidates": hits[:20],
            "body": body[:2500],
        })

    print(json.dumps({
        "scanned": len(ids),
        "query": q,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "messages": out,
    }, indent=1))

    if not args.no_commit and not args.all:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        # Gmail's after: takes a date; use today so the next run starts here.
        STATE.write_text(dt.date.today().isoformat())


if __name__ == "__main__":
    main()
