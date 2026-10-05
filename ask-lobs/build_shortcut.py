#!/usr/bin/env python3
"""Build and sign the two iOS Shortcuts that front the ask-lobs server.

Ask Lobs       Ask for Input (Siri dictates) -> POST /ask {"q"} -> Show Result
               (Siri reads the reply aloud).
Share to Lobs  Share-sheet shortcut: takes the shared item (screenshot, photo,
               PDF, link or text), asks "Anything to add?", POSTs
               /share {"note", "text", "data" (base64)} -> Show Result.

  build_shortcut.py ASK_URL   writes ~/.hermes/ask-lobs/{Ask Lobs,Share to Lobs}.shortcut
                              (signed) and mints a 48 h install link

The bearer token is read from ~/.hermes/ask-lobs/token and embedded in both
files. It is never printed.
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


def new_id():
    return str(uuid.uuid4()).upper()


def text(s):
    return {"Value": {"string": s, "attachmentsByRange": {}}, "WFSerializationType": "WFTextTokenString"}


def _token(attachment):
    return {"Value": {"string": OBJ, "attachmentsByRange": {"{0, 1}": attachment}},
            "WFSerializationType": "WFTextTokenString"}


def var_text(output_name, output_uuid):
    return _token({"OutputName": output_name, "OutputUUID": output_uuid, "Type": "ActionOutput"})


def input_text():
    return _token({"Type": "ExtensionInput"})


def dict_field(items):
    return {"Value": {"WFDictionaryFieldValueItems": [
        {"WFItemType": 0, "WFKey": text(k), "WFValue": v} for k, v in items]},
        "WFSerializationType": "WFDictionaryFieldValue"}


def post(url, token, json_items, fetch_id):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": fetch_id,
                "WFURL": url,
                "WFHTTPMethod": "POST",
                "ShowHeaders": True,
                "WFHTTPHeaders": dict_field([("Authorization", text("Bearer " + token))]),
                "WFHTTPBodyType": "JSON",
                "WFJSONValues": dict_field(json_items),
            }}


def show(fetch_id):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
            "WFWorkflowActionParameters": {"Text": var_text("Contents of URL", fetch_id)}}


def workflow(actions, types, inputs, glyph):
    return {
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": "2607.0.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": "ActionExtension" in types,
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": glyph},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": inputs,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": types,
        "WFQuickActionSurfaces": [],
    }


def build_ask(url, token):
    ask_id, fetch_id = new_id(), new_id()
    return workflow([
        {"WFWorkflowActionIdentifier": "is.workflow.actions.ask",
         "WFWorkflowActionParameters": {"UUID": ask_id, "WFAskActionPrompt": "What do you need?",
                                        "WFInputType": "Text"}},
        post(url, token, [("q", var_text("Provided Input", ask_id))], fetch_id),
        show(fetch_id),
    ], ["Watch"], ["WFStringContentItem"], 59511)


def build_share(url, token):
    ask_id, b64_id, fetch_id = new_id(), new_id(), new_id()
    return workflow([
        {"WFWorkflowActionIdentifier": "is.workflow.actions.ask",
         "WFWorkflowActionParameters": {"UUID": ask_id, "WFAskActionPrompt": "Anything to add? (Done to skip)",
                                        "WFInputType": "Text", "WFAskActionDefaultAnswer": ""}},
        # Images and PDFs travel as base64; links and text also go as plain text,
        # because coercing a URL to a file can fetch the page instead.
        {"WFWorkflowActionIdentifier": "is.workflow.actions.base64encode",
         "WFWorkflowActionParameters": {"UUID": b64_id, "WFEncodeMode": "Encode",
                                        "WFBase64LineBreakMode": "None",
                                        "WFInput": {"Value": {"Type": "ExtensionInput"},
                                                    "WFSerializationType": "WFTextTokenAttachment"}}},
        post(url.rsplit("/ask", 1)[0] + "/share", token, [
            ("note", var_text("Provided Input", ask_id)),
            ("text", input_text()),
            ("data", var_text("Base64 Encoded", b64_id)),
        ], fetch_id),
        show(fetch_id),
    ], ["ActionExtension"],
        ["WFImageContentItem", "WFPDFContentItem", "WFURLContentItem", "WFStringContentItem",
         "WFSafariWebPageContentItem", "WFGenericFileContentItem"], 59446)


def sign(name, plist):
    unsigned = STATE / f"{name}.unsigned.shortcut"
    signed = STATE / f"{name}.shortcut"
    with unsigned.open("wb") as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY)
    unsigned.chmod(0o600)
    r = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned),
                        "--output", str(signed)], capture_output=True, text=True, timeout=120)
    unsigned.unlink(missing_ok=True)
    if r.returncode != 0 or not signed.exists():
        sys.exit(f"signing {name} failed: " + (r.stderr or r.stdout).strip()[:400])
    signed.chmod(0o600)
    print(f"signed: {signed} ({signed.stat().st_size} bytes)")


def main():
    url = sys.argv[1]
    token = (STATE / "token").read_text().strip()
    sign("Ask Lobs", build_ask(url, token))
    sign("Share to Lobs", build_share(url, token))
    nonce = secrets.token_urlsafe(18)
    link = STATE / "shortcut-link"
    link.write_text(f"{nonce} {time.time() + LINK_TTL_S:.0f}")
    link.chmod(0o600)
    base = url.rsplit("/ask", 1)[0]
    print(f"install links (tailnet only, expire in {LINK_TTL_S // 3600}h):")
    print(f"  {base}/shortcut/{nonce}        Ask Lobs")
    print(f"  {base}/shortcut/{nonce}/share  Share to Lobs")


if __name__ == "__main__":
    main()
