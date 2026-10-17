#!/usr/bin/env python3
"""Proton Mail MCP server — talks to Proton Mail Bridge over local IMAP/SMTP.

Supports multiple Proton accounts. Accounts are read from
~/.config/proton-mail-mcp/accounts.json:

    {"accounts": [
        {"name": "personal", "email": "user@example.com", "password": "<bridge password>"},
        ...
    ]}

The password is the *Bridge* password shown by `bridge --cli` (`info`), not
your Proton login password.

Transports:
    python server.py                  -> stdio (Antigravity / Claude Code / Cursor)
    python server.py --http [--port]  -> streamable HTTP at /mcp (ChatGPT connectors),
                                         protected by MCP_TOKEN bearer auth.
"""
from __future__ import annotations

import argparse
import email
import imaplib
import json
import os
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

CONFIG = Path(os.environ.get("PROTON_MCP_CONFIG", "~/.config/proton-mail-mcp/accounts.json")).expanduser()
BRIDGE_CERT = Path("~/.config/protonmail/bridge-v3/cert.pem").expanduser()

# Optional "login": IMAP/SMTP username when it differs from the From address (e.g. a Proton custom-domain
# alias on the same account: login = the primary address, email = the alias).
# Provider presets. An account picks one with "provider": "gmail" | "proton" (default proton).
PROVIDERS = {
    "proton": {"imap_host": "127.0.0.1", "imap_port": 1143, "imap_ssl": False,
               "smtp_host": "127.0.0.1", "smtp_port": 1025, "smtp_ssl": False,
               "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
    "gmail":  {"imap_host": "imap.gmail.com", "imap_port": 993, "imap_ssl": True,
               "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_ssl": True,
               "trash": "[Gmail]/Trash", "drafts": "[Gmail]/Drafts", "archive": "[Gmail]/All Mail"},
    # hydroxide: open-source Proton bridge (works with free plans). IMAP 1144 / SMTP 1026, plain on loopback.
    "hydroxide": {"imap_host": "127.0.0.1", "imap_port": 1144, "imap_ssl": False, "imap_starttls": False,
                  "smtp_host": "127.0.0.1", "smtp_port": 1026, "smtp_ssl": False, "smtp_starttls": False,
                  "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
    "generic": {"imap_port": 993, "imap_ssl": True, "smtp_port": 465, "smtp_ssl": True,
                "trash": "Trash", "drafts": "Drafts", "archive": "Archive"},
}


def _cfg(acct: dict) -> dict:
    return {**PROVIDERS[acct.get("provider", "proton")], **acct}

mcp = FastMCP("mail", instructions=(
    "Read, search and send email through the user's Proton Mail accounts. "
    "Call list_accounts first to learn account names; every other tool takes an "
    "`account` argument (defaults to the first account). Folder names are IMAP "
    "names such as INBOX, Sent, Drafts, Trash (Proton) or INBOX, [Gmail]/Sent Mail, [Gmail]/Trash (Gmail); "
    "call list_folders to see them."
))


# --------------------------------------------------------------------------- config
def _load_accounts() -> list[dict]:
    if not CONFIG.exists():
        raise RuntimeError(f"No accounts configured. Run setup.sh or create {CONFIG}")
    data = json.loads(CONFIG.read_text())
    accts = data["accounts"] if isinstance(data, dict) else data
    if not accts:
        raise RuntimeError(f"{CONFIG} has no accounts")
    return accts


def _account(name: str | None) -> dict:
    accts = _load_accounts()
    if not name:
        return accts[0]
    for a in accts:
        if name.lower() in (a.get("name", "").lower(), a["email"].lower()):
            return a
    raise ValueError(f"Unknown account '{name}'. Known: {[a.get('name') or a['email'] for a in accts]}")


def _ssl_ctx(acct: dict | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if acct and _cfg(acct)["imap_host"] != "127.0.0.1":
        return ctx  # real provider: normal verification
    if BRIDGE_CERT.exists():
        ctx.load_verify_locations(str(BRIDGE_CERT))
        ctx.check_hostname = False  # cert is for 127.0.0.1; python hostname check is picky
    else:  # bridge is loopback-only; unverified is acceptable fallback
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


# --------------------------------------------------------------------------- imap helpers
class _Imap:
    def __init__(self, acct: dict, folder: str = "INBOX", readonly: bool = True):
        self.acct, self.folder, self.readonly = acct, folder, readonly

    def __enter__(self) -> imaplib.IMAP4:
        self.conn = _imap_connect(self.acct)
        typ, _ = self.conn.select(_quote(self.folder), readonly=self.readonly)
        if typ != "OK":
            raise ValueError(f"Folder not found: {self.folder}")
        return self.conn

    def __exit__(self, *exc):
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.conn.logout()
        except Exception:
            pass


def _imap_connect(acct: dict) -> imaplib.IMAP4:
    c = _cfg(acct)
    if c["imap_ssl"]:
        conn = imaplib.IMAP4_SSL(c["imap_host"], c["imap_port"], ssl_context=_ssl_ctx(acct))
    else:
        conn = imaplib.IMAP4(c["imap_host"], c["imap_port"])
        if c.get("imap_starttls", True):
            conn.starttls(_ssl_ctx(acct))
    conn.login(acct.get("login") or acct["email"], acct["password"])
    return conn


def _smtp_connect(acct: dict) -> smtplib.SMTP:
    c = _cfg(acct)
    if c["smtp_ssl"]:
        s = smtplib.SMTP_SSL(c["smtp_host"], c["smtp_port"], timeout=60, context=_ssl_ctx(acct))
    else:
        s = smtplib.SMTP(c["smtp_host"], c["smtp_port"], timeout=60)
        if c.get("smtp_starttls", True):
            s.starttls(context=_ssl_ctx(acct))
    s.login(acct.get("login") or acct["email"], acct["password"])
    return s


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _hdr(v: Any) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return str(v)


def _body_text(msg: email.message.Message, max_chars: int) -> str:
    plain, html = None, None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        ct = part.get_content_type()
        try:
            payload = part.get_payload(decode=True)
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace") if payload else ""
        except Exception:
            continue
        if ct == "text/plain" and plain is None:
            plain = text
        elif ct == "text/html" and html is None:
            html = text
    text = plain if plain is not None else _strip_html(html or "")
    return text[:max_chars] + ("\n…[truncated]" if len(text) > max_chars else "")


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    import html as _html
    return re.sub(r"\n{3,}", "\n\n", _html.unescape(h)).strip()


def _summary(uid: str, msg: email.message.Message, flags: str = "") -> dict:
    try:
        date = parsedate_to_datetime(msg.get("Date")).isoformat()
    except Exception:
        date = msg.get("Date", "")
    return {
        "uid": uid,
        "from": _hdr(msg.get("From")),
        "to": _hdr(msg.get("To")),
        "subject": _hdr(msg.get("Subject")),
        "date": date,
        "unread": "\\Seen" not in flags,
        "has_attachments": any(p.get_filename() for p in msg.walk()),
    }


def _fetch_summaries(conn: imaplib.IMAP4, uids: list[bytes]) -> list[dict]:
    if not uids:
        return []
    typ, data = conn.uid("fetch", b",".join(uids), "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, tuple):
            continue
        meta = item[0].decode(errors="replace")
        if i + 1 < len(data) and isinstance(data[i + 1], bytes):
            meta += " " + data[i + 1].decode(errors="replace")
        uid = re.search(r"UID (\d+)", meta).group(1)
        flags = re.search(r"FLAGS \(([^)]*)\)", meta)
        out.append(_summary(uid, email.message_from_bytes(item[1]), flags.group(1) if flags else ""))
    return out


# --------------------------------------------------------------------------- tools
@mcp.tool()
def list_accounts() -> list[dict]:
    """List configured mail accounts (name, email, provider)."""
    return [{"name": a.get("name") or a["email"], "email": a["email"], "provider": a.get("provider", "proton")}
            for a in _load_accounts()]


@mcp.tool()
def list_folders(account: str | None = None) -> list[dict]:
    """List mail folders/labels for an account, with message and unread counts."""
    acct = _account(account)
    conn = _imap_connect(acct)
    try:
        typ, boxes = conn.list()
        out = []
        for raw in boxes or []:
            m = re.match(rb'\((?P<flags>[^)]*)\) "(?P<delim>[^"]*)" (?P<name>.+)$', raw)
            if not m:
                continue
            name = m.group("name").decode().strip('"')
            if b"\\Noselect" in m.group("flags"):
                continue
            typ, st = conn.status(_quote(name), "(MESSAGES UNSEEN)")
            nums = dict(re.findall(r"(MESSAGES|UNSEEN) (\d+)", st[0].decode())) if typ == "OK" else {}
            out.append({"folder": name, "messages": int(nums.get("MESSAGES", 0)), "unread": int(nums.get("UNSEEN", 0))})
        return out
    finally:
        conn.logout()


@mcp.tool()
def list_messages(account: str | None = None, folder: str = "INBOX", limit: int = 20, unread_only: bool = False) -> list[dict]:
    """List the most recent messages in a folder (newest first). Returns uid, from, subject, date, unread."""
    with _Imap(_account(account), folder) as conn:
        typ, data = conn.uid("search", None, "UNSEEN" if unread_only else "ALL")
        uids = data[0].split()[-max(1, min(limit, 200)):]
        return list(reversed(_fetch_summaries(conn, uids)))


@mcp.tool()
def search_messages(query: str, account: str | None = None, folder: str = "INBOX", limit: int = 20,
                    sender: str | None = None, since: str | None = None) -> list[dict]:
    """Search messages. `query` matches subject or body text; optional `sender` (address substring)
    and `since` (date like 2026-08-01). Set query to "" to filter by sender/since only."""
    crit: list[str] = []
    if query:
        crit += ["OR", "SUBJECT", _qs(query), "BODY", _qs(query)]
    if sender:
        crit += ["FROM", _qs(sender)]
    if since:
        from datetime import date
        d = date.fromisoformat(since)
        crit += ["SINCE", d.strftime("%d-%b-%Y")]
    if not crit:
        crit = ["ALL"]
    with _Imap(_account(account), folder) as conn:
        typ, data = conn.uid("search", None, *crit)
        uids = data[0].split()[-max(1, min(limit, 200)):]
        return list(reversed(_fetch_summaries(conn, uids)))


def _qs(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


@mcp.tool()
def read_message(uid: str, account: str | None = None, folder: str = "INBOX", max_chars: int = 20000,
                 mark_as_read: bool = False) -> dict:
    """Read a full message by uid: headers, plain-text body, attachment names."""
    with _Imap(_account(account), folder, readonly=not mark_as_read) as conn:
        typ, data = conn.uid("fetch", uid, "(FLAGS RFC822)" if mark_as_read else "(FLAGS BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            raise ValueError(f"Message uid {uid} not found in {folder}")
        meta = data[0][0].decode(errors="replace") + " ".join(x.decode(errors="replace") for x in data[1:] if isinstance(x, bytes))
        flags = re.search(r"FLAGS \(([^)]*)\)", meta)
        msg = email.message_from_bytes(data[0][1])
        out = _summary(uid, msg, flags.group(1) if flags else "")
        out.update({
            "cc": _hdr(msg.get("Cc")),
            "reply_to": _hdr(msg.get("Reply-To")),
            "message_id": msg.get("Message-ID", ""),
            "attachments": [_hdr(p.get_filename()) for p in msg.walk() if p.get_filename()],
            "body": _body_text(msg, max_chars),
        })
        return out


@mcp.tool()
def send_email(to: str, subject: str, body: str, account: str | None = None, cc: str = "", bcc: str = "",
               html: bool = False, in_reply_to: str = "") -> str:
    """Send an email from the given account. `to`/`cc`/`bcc` are comma-separated addresses.
    Set `in_reply_to` to a Message-ID to thread as a reply."""
    acct = _account(account)
    msg = EmailMessage()
    msg["From"] = acct["email"]
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if html:
        msg.set_content(_strip_html(body))
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    rcpts = [parseaddr(a)[1] for a in re.split(r"[,;]", f"{to},{cc},{bcc}") if a.strip()]
    with _smtp_connect(acct) as s:
        s.send_message(msg, from_addr=acct["email"], to_addrs=rcpts)
    return f"Sent to {', '.join(rcpts)} from {acct['email']}"


@mcp.tool()
def reply_to_message(uid: str, body: str, account: str | None = None, folder: str = "INBOX",
                     reply_all: bool = False) -> str:
    """Reply to a message by uid (quotes nothing; write the full reply in `body`)."""
    acct = _account(account)
    orig = read_message(uid, account, folder, max_chars=1)
    to = orig["reply_to"] or orig["from"]
    cc = ""
    if reply_all:
        others = [a for a in re.split(r"[,;]", f"{orig['to']},{orig['cc']}") if a.strip() and acct["email"] not in a]
        cc = ", ".join(others)
    subj = orig["subject"] if orig["subject"].lower().startswith("re:") else f"Re: {orig['subject']}"
    return send_email(to, subj, body, account, cc=cc, in_reply_to=orig["message_id"])


@mcp.tool()
def mark_message(uid: str, read: bool | None = None, flagged: bool | None = None,
                 account: str | None = None, folder: str = "INBOX") -> str:
    """Mark a message read/unread and/or starred/unstarred."""
    with _Imap(_account(account), folder, readonly=False) as conn:
        if read is not None:
            conn.uid("store", uid, "+FLAGS" if read else "-FLAGS", "(\\Seen)")
        if flagged is not None:
            conn.uid("store", uid, "+FLAGS" if flagged else "-FLAGS", "(\\Flagged)")
    return f"Updated {uid}"


@mcp.tool()
def move_message(uid: str, to_folder: str, account: str | None = None, folder: str = "INBOX") -> str:
    """Move a message to another folder (e.g. Archive, Trash, Spam, Folders/Receipts)."""
    with _Imap(_account(account), folder, readonly=False) as conn:
        typ, _ = conn.uid("move", uid, _quote(to_folder))
        if typ != "OK":
            typ, _ = conn.uid("copy", uid, _quote(to_folder))
            if typ != "OK":
                raise ValueError(f"Could not move to {to_folder}")
            conn.uid("store", uid, "+FLAGS", "(\\Deleted)")
            conn.expunge()
    return f"Moved {uid} from {folder} to {to_folder}"


@mcp.tool()
def delete_message(uid: str, account: str | None = None, folder: str = "INBOX") -> str:
    """Move a message to Trash."""
    return move_message(uid, _cfg(_account(account))["trash"], account, folder)


@mcp.tool()
def save_draft(to: str, subject: str, body: str, account: str | None = None) -> str:
    """Save a draft in the Drafts folder without sending."""
    acct = _account(account)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = acct["email"], to, subject
    msg.set_content(body)
    conn = _imap_connect(acct)
    try:
        typ, _ = conn.append(_quote(_cfg(acct)["drafts"]), "(\\Draft)", None, msg.as_bytes())
        if typ != "OK":
            raise RuntimeError("Bridge refused APPEND to Drafts")
    finally:
        conn.logout()
    return "Draft saved"


# --------------------------------------------------------------------------- entrypoint
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP on /mcp (for ChatGPT)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8972)
    args = ap.parse_args()
    if not args.http:
        mcp.run()
        return

    token = os.environ.get("MCP_TOKEN")
    # MCP_NOAUTH=1: skip bearer check (use when the secret lives in an unguessable
    # Tailscale Funnel path, e.g. for ChatGPT which cannot send custom headers).
    noauth = os.environ.get("MCP_NOAUTH") == "1"
    if not token and not noauth:
        raise SystemExit("MCP_TOKEN env var required in --http mode (or MCP_NOAUTH=1)")
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class Bearer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if not noauth and request.headers.get("authorization") != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    from mcp.server.transport_security import TransportSecuritySettings
    # Public hostname comes through Tailscale Funnel; bearer auth already guards the endpoint.
    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp.settings.host, mcp.settings.port = args.host, args.port
    mcp.settings.streamable_http_path = "/mcp"
    app = mcp.streamable_http_app()
    app.add_middleware(Bearer)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
