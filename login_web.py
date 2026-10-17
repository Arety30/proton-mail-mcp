#!/usr/bin/env python3
"""Tiny local web UI (http://localhost:55) for Proton Mail MCP.

- "Sign in to Proton Mail" button -> JS redirect to Proton's login URL.
- Form to register accounts (email + Bridge password) for the MCP server.
- Shows configured accounts and whether Bridge is reachable.
"""
import html
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

CFG = Path("~/.config/proton-mail-mcp/accounts.json").expanduser()
PROTON_LOGIN = "https://account.proton.me/mail"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 55


def load():
    return json.loads(CFG.read_text())["accounts"] if CFG.exists() else []


def save(accts):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps({"accounts": accts}, indent=2))
    CFG.chmod(0o600)


def bridge_up():
    try:
        socket.create_connection(("127.0.0.1", 1143), timeout=1).close()
        return True
    except OSError:
        return False


PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Proton Mail MCP</title>
<style>
body{{font:15px system-ui;background:#1b1340;color:#eee;max-width:620px;margin:40px auto;padding:0 16px}}
.card{{background:#2a1f5e;border-radius:12px;padding:20px;margin-bottom:18px}}
button,input{{font:inherit;border-radius:8px;border:0;padding:10px 14px}}
button{{background:#6d4aff;color:#fff;cursor:pointer}} button.small{{padding:4px 10px;background:#a33}}
input{{width:100%;box-sizing:border-box;margin:4px 0 12px;background:#150e33;color:#fff}}
.ok{{color:#7f7}} .bad{{color:#f77}} code{{background:#150e33;padding:2px 6px;border-radius:4px}}
</style></head><body>
<h1>Proton Mail MCP</h1>
<div class=card><h3>Step 1 — Proton Mail login</h3>
<button onclick="protonLogin()">Sign in to Proton Mail</button>
<p>Bridge: <span class="{cls}">{bridge}</span>. To link an account to Bridge run
<code>~/proton-mail-mcp/setup.sh</code> once, then type <code>info</code> to see each Bridge password.</p></div>
<div class=card><h3>Step 2 — Register account for the MCP</h3>
<form method=post action=/add>
<label>Provider</label>
<select name=provider style="width:100%;padding:10px;border-radius:8px;margin:4px 0 12px;background:#150e33;color:#fff;border:0">
<option value=proton>Proton (via Bridge — paid plans only)</option>
<option value=gmail>Gmail (App Password)</option></select>
<label>Short name</label><input name=name placeholder="personal" required>
<label>Email</label><input name=email type=email placeholder="you@proton.me / you@gmail.com" required>
<label>Password — Proton: Bridge password from <code>info</code>; Gmail: 16-char App Password</label>
<input name=password type=password required>
<button>Save account</button></form>
<p style="font-size:13px;opacity:.8">Gmail App Password: <a style="color:#b9a7ff" href="https://myaccount.google.com/apppasswords" target=_blank>myaccount.google.com/apppasswords</a>
(needs 2-Step Verification on). <button type=button class=small style="background:#6d4aff" onclick="window.open('https://accounts.google.com/','_blank')">Sign in to Google</button></p></div>
<div class=card><h3>Configured accounts</h3>{rows}</div>
<script>function protonLogin(){{window.open("{login}","_blank")}}</script>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, code=200, ctype="text/html; charset=utf-8", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.path == "/login":
            return self._send("", 302, extra=[("Location", PROTON_LOGIN)])
        if self.path == "/accounts.json":
            return self._send(json.dumps([{"name": a["name"], "email": a["email"]} for a in load()]), ctype="application/json")
        accts = load()
        rows = "".join(
            f"<p>{html.escape(a['name'])} — {html.escape(a['email'])} <small>({a.get('provider','proton')})</small> "
            f"<form style='display:inline' method=post action=/remove><input type=hidden name=email value='{html.escape(a['email'])}'>"
            f"<button class=small>remove</button></form></p>" for a in accts) or "<p>none yet</p>"
        up = bridge_up()
        self._send(PAGE.format(rows=rows, login=PROTON_LOGIN, bridge="running" if up else "not running",
                               cls="ok" if up else "bad"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        f = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        accts = [a for a in load() if a["email"] != f.get("email")]
        if self.path == "/add":
            accts.append({"name": f["name"], "email": f["email"], "password": f["password"].replace(" ", ""),
                          "provider": f.get("provider", "proton")})
        save(accts)
        self._send("", 303, extra=[("Location", "/")])


if __name__ == "__main__":
    print(f"http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
