#!/usr/bin/env python3
"""Build and sign the "Ask Lobs" iOS Shortcut.

Ask for Input (Siri speaks the prompt and dictates the reply) -> POST it as
JSON to the ask-lobs server over the tailnet with the bearer token -> Show
Result (Siri reads the answer aloud).

  build_shortcut.py URL   writes ~/.hermes/ask-lobs/Ask Lobs.shortcut (signed)

The token is read from ~/.hermes/ask-lobs/token and embedded in the file. It is
never printed.
"""
import pathlib
import plistlib
import secrets
import subprocess
import sys
import time
import uuid

STATE = pathlib.Path.home() / ".hermes" / "ask-lobs"
LINK_TTL_S = 48 * 3600
OBJ = "\ufffc"  # attachment placeholder Shortcuts uses for variables in text


def text(s):
    return {"Value": {"string": s, "attachmentsByRange": {}}, "WFSerializationType": "WFTextTokenString"}


def var_text(output_name, output_uuid):
    return {"Value": {"string": OBJ, "attachmentsByRange": {
        "{0, 1}": {"OutputName": output_name, "OutputUUID": output_uuid, "Type": "ActionOutput"}}},
        "WFSerializationType": "WFTextTokenString"}


def dict_field(items):
    return {"Value": {"WFDictionaryFieldValueItems": [
        {"WFItemType": 0, "WFKey": text(k), "WFValue": v} for k, v in items]},
        "WFSerializationType": "WFDictionaryFieldValue"}


def build(url, token):
    ask_id, fetch_id = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
    actions = [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.ask",
         "WFWorkflowActionParameters": {"UUID": ask_id, "WFAskActionPrompt": "What do you need?",
                                        "WFInputType": "Text"}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
         "WFWorkflowActionParameters": {
             "UUID": fetch_id,
             "WFURL": url,
             "WFHTTPMethod": "POST",
             "ShowHeaders": True,
             "WFHTTPHeaders": dict_field([("Authorization", text("Bearer " + token))]),
             "WFHTTPBodyType": "JSON",
             "WFJSONValues": dict_field([("q", var_text("Provided Input", ask_id))]),
         }},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
         "WFWorkflowActionParameters": {"Text": var_text("Contents of URL", fetch_id)}},
    ]
    return {
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": "2607.0.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": ["Watch"],
        "WFQuickActionSurfaces": [],
    }


def main():
    url = sys.argv[1]
    token = (STATE / "token").read_text().strip()
    unsigned = STATE / "Ask Lobs.unsigned.shortcut"
    signed = STATE / "Ask Lobs.shortcut"
    with unsigned.open("wb") as f:
        plistlib.dump(build(url, token), f, fmt=plistlib.FMT_BINARY)
    unsigned.chmod(0o600)
    r = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned),
                        "--output", str(signed)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not signed.exists():
        sys.exit("signing failed: " + (r.stderr or r.stdout).strip()[:400])
    signed.chmod(0o600)
    unsigned.unlink()
    nonce = secrets.token_urlsafe(18)
    link = STATE / "shortcut-link"
    link.write_text(f"{nonce} {time.time() + LINK_TTL_S:.0f}")
    link.chmod(0o600)
    base = url.rsplit("/ask", 1)[0]
    print(f"signed shortcut: {signed} ({signed.stat().st_size} bytes)")
    print(f"install link (tailnet only, expires in {LINK_TTL_S // 3600}h): {base}/shortcut/{nonce}")


if __name__ == "__main__":
    main()
