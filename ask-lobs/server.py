#!/usr/bin/env python3
"""Ask Lobs: a tiny HTTP front door so Rafe can talk to Lobs from Siri.

The iPhone Shortcut "Ask Lobs" dictates a question, POSTs it here over the
tailnet, and speaks the answer. Each question runs a real Hermes agent turn
(`hermes chat -Q -q ...`) with full tools, in a per-day session ("siri-YYYY-MM-DD")
so a follow-up ("and Tuesday?") keeps context.

If the turn finishes within SYNC_BUDGET seconds the answer comes back in the
HTTP response. Otherwise the response says so immediately and the answer is
delivered to his Discord DM when it lands, because an iOS URL request gives up
after about a minute and Siri sooner.

Auth: tailnet-only (tailscale serve) plus a bearer token kept in
~/.hermes/ask-lobs/token (0600, never logged). The token is baked into the
Shortcut, which is served from GET /shortcut on the same tailnet-only origin.

  POST /ask               body: {"q": "..."} or raw text   -> text/plain answer
  GET  /health            -> ok
  GET  /shortcut/<nonce>  -> signed "Ask Lobs.shortcut"; the link expires (see build_shortcut.py)
"""
import datetime as dt
import hmac
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = pathlib.Path.home()
STATE = HOME / ".hermes" / "ask-lobs"
TOKEN = (STATE / "token").read_text().strip()
SHORTCUT = STATE / "Ask Lobs.shortcut"
# The shortcut file embeds the bearer token, so it is downloadable only through
# a random path that expires (written by build_shortcut.py as "<nonce> <unix expiry>").
SHORTCUT_LINK = STATE / "shortcut-link"
LOG = STATE / "asks.jsonl"
HERMES = str(HOME / ".hermes/hermes-agent/venv/bin/hermes")
DELIVER = os.environ.get("ASK_LOBS_DELIVER", "discord")  # home channel = Rafe's DM
PORT = int(os.environ.get("ASK_LOBS_PORT", "8660"))
SYNC_BUDGET = float(os.environ.get("ASK_LOBS_SYNC_SECONDS", "45"))
RUN_BUDGET = 900

FRAME = (
    "[Rafe asked this by voice through Siri on his phone; your reply is read aloud. "
    "Answer in at most three short spoken sentences: plain text, no markdown, no lists, "
    "no URLs, no code. If it needs real work (calendar, tasks, reminders, repos, web), "
    "do the work first, then say the result. If something truly needs his decision, "
    "ask one short question.]\n\n"
)

# One agent turn at a time: two concurrent `--continue` runs on the same session
# would interleave their writes to its history.
_turn_lock = threading.Lock()


def log(rec):
    rec["at"] = dt.datetime.now().isoformat(timespec="seconds")
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def clean(text):
    lines = [l for l in text.splitlines()
             if not l.startswith("session_id:") and not re.match(r"Session \S+ found but has no messages", l)]
    out = "\n".join(lines).strip()
    # Belt and braces for speech: strip markdown the model may still emit.
    out = re.sub(r"[*_`#>]+", "", out)
    return out or "I finished, but had nothing to say back."


class Turn:
    def __init__(self, question):
        self.question = question
        self.answer = None
        self.error = None
        self.done = threading.Event()
        self.detached = False  # True once the HTTP caller stopped waiting

    def run(self):
        session = "siri-" + dt.date.today().isoformat()
        try:
            with _turn_lock:
                r = subprocess.run(
                    [HERMES, "chat", "-Q", "-q", FRAME + self.question,
                     "--continue", session, "--create-if-missing", "--source", "siri",
                     "--run-budget", str(RUN_BUDGET)],
                    capture_output=True, text=True, timeout=RUN_BUDGET + 60, cwd=str(HOME))
            if r.returncode != 0 and not r.stdout.strip():
                self.error = (r.stderr.strip().splitlines() or ["hermes exited %d" % r.returncode])[-1][:300]
            else:
                self.answer = clean(r.stdout)
        except Exception as e:  # noqa: BLE001 - report any failure to the phone
            self.error = str(e)[:300]
        finally:
            self.done.set()
            log({"q": self.question, "a": self.answer, "err": self.error, "async": self.detached})
            if self.detached:
                body = self.answer or ("That one failed: " + (self.error or "unknown error"))
                subprocess.run([HERMES, "send", "-t", DELIVER, "-s", "🎙 " + self.question[:180], body],
                               capture_output=True, text=True, timeout=120)


class Handler(BaseHTTPRequestHandler):
    server_version = "ask-lobs/1"

    def log_message(self, format, *args):  # noqa: A002 - keep the request line, never headers
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), format % args))

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), ("Bearer " + TOKEN).encode())

    def _shortcut_link_ok(self, nonce):
        try:
            want, expiry = SHORTCUT_LINK.read_text().split()
        except (OSError, ValueError):
            return False
        return hmac.compare_digest(nonce.encode(), want.encode()) and time.time() < float(expiry)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, "ok")
        if self.path.startswith("/shortcut/"):
            if not SHORTCUT.exists() or not self._shortcut_link_ok(self.path[len("/shortcut/"):].split("?")[0]):
                return self._send(404, "not found")
            self.send_response(200)
            data = SHORTCUT.read_bytes()
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="Ask Lobs.shortcut"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        return self._send(404, "not found")

    def do_POST(self):
        if self.path != "/ask":
            return self._send(404, "not found")
        if not self._authed():
            return self._send(401, "unauthorized")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 20000)).decode("utf-8", "replace").strip()
        q = raw
        if raw.startswith("{"):
            try:
                q = str(json.loads(raw).get("q", "")).strip()
            except ValueError:
                pass
        if not q:
            return self._send(400, "I didn't catch a question.")
        if _turn_lock.locked():
            busy = True
        else:
            busy = False
        turn = Turn(q)
        threading.Thread(target=turn.run, daemon=True).start()
        if not busy and turn.done.wait(SYNC_BUDGET):
            return self._send(200, turn.answer or ("That failed: " + (turn.error or "unknown error")))
        turn.detached = True
        if turn.done.is_set():  # finished in the instant between the wait and the flag
            return self._send(200, turn.answer or ("That failed: " + (turn.error or "unknown error")))
        msg = ("I'm still finishing something else; I'll send this answer to Discord."
               if busy else "This one's taking a bit. I'll send the answer to Discord.")
        return self._send(200, msg)


if __name__ == "__main__":
    STATE.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"ask-lobs listening on 127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
