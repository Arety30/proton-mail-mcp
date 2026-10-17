#!/usr/bin/env python3
"""Log every account from ~/.config/proton-mail-mcp/proton.env into Proton Mail Bridge
(headless), fetch the Bridge passwords and write accounts.json for the MCP server.

Usage:  uv run python auto_login.py        (re-runnable; already-logged-in accounts are skipped)
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pexpect

ENV = Path("~/.config/proton-mail-mcp/proton.env").expanduser()
CFG = Path("~/.config/proton-mail-mcp/accounts.json").expanduser()
BRIDGE = "/usr/lib/protonmail/bridge/bridge"
PROMPT = r">>> "


def load_env() -> list[dict]:
    env = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    accts = []
    for i in range(1, 10):
        email = env.get(f"PROTON_{i}_EMAIL")
        if not email:
            continue
        accts.append({
            "name": env.get(f"PROTON_{i}_NAME") or email.split("@")[0],
            "email": email,
            "password": env.get(f"PROTON_{i}_PASSWORD", ""),
            "totp": env.get(f"PROTON_{i}_TOTP_SECRET", ""),
            "mailbox_password": env.get(f"PROTON_{i}_MAILBOX_PASSWORD", ""),
        })
    return accts


def totp(secret: str) -> str:
    import pyotp
    return pyotp.TOTP(secret.replace(" ", "")).now()


def bridge_info(cli) -> dict[str, str]:
    """Return {email: bridge_password} from the `info` command."""
    cli.sendline("info")
    cli.expect(PROMPT, timeout=30)
    out = cli.before
    creds, cur = {}, None
    for line in out.splitlines():
        m = re.search(r"Username:\s*(\S+@\S+)", line)
        if m:
            cur = m.group(1).lower()
        m = re.search(r"Password:\s*(\S+)", line)
        if m and cur and cur not in creds:
            creds[cur] = m.group(1)
    return creds


def login(cli, a: dict) -> None:
    cli.sendline("login")
    cli.expect(r"(?i)username", timeout=30)
    cli.sendline(a["email"])
    cli.expect(r"(?i)password", timeout=30)
    cli.sendline(a["password"])
    while True:
        i = cli.expect([r"(?i)(two.?factor|2fa).*?:", r"(?i)mailbox password.*?:", PROMPT,
                        r"(?i)CAPTCHA[^\n]*", r"(?i)(incorrect|invalid|failed|ERRO)[^\n]*"], timeout=120)
        if i == 0:
            if not a["totp"]:
                raise SystemExit(f"{a['email']}: 2FA required — set PROTON_n_TOTP_SECRET in {ENV}")
            cli.sendline(totp(a["totp"]))
        elif i == 1:
            cli.sendline(a["mailbox_password"] or a["password"])
        elif i == 2:
            return
        elif i == 3:
            raise SystemExit(f"{a['email']}: Proton demands a CAPTCHA for this login. Log this account in once via the "
                             "Bridge GUI (run: protonmail-bridge), then re-run this script — it will pick it up.")
        else:
            raise SystemExit(f"{a['email']}: login failed: {cli.after.strip()}")


def main() -> None:
    if not ENV.exists():
        sys.exit(f"missing {ENV}")
    wanted = [a for a in load_env() if a["password"]]
    if not wanted:
        sys.exit(f"no accounts with a password in {ENV}")

    subprocess.run(["systemctl", "--user", "stop", "proton-bridge.service"], capture_output=True)
    time.sleep(1)
    cli = pexpect.spawn(BRIDGE, ["--cli"], encoding="utf-8", timeout=120, env={**os.environ, "TERM": "dumb"})
    cli.logfile_read = sys.stdout if "-v" in sys.argv else None
    cli.expect(PROMPT)

    have = bridge_info(cli)
    for a in wanted:
        if a["email"].lower() in have:
            print(f"already in Bridge: {a['email']}")
            continue
        print(f"logging in {a['email']} ...")
        login(cli, a)
        time.sleep(2)
    have = bridge_info(cli)
    cli.sendline("exit")
    cli.expect(pexpect.EOF, timeout=30)

    accounts = []
    for a in wanted:
        bp = have.get(a["email"].lower())
        if not bp:
            print(f"WARNING: no Bridge password found for {a['email']}")
            continue
        accounts.append({"name": a["name"], "email": a["email"], "password": bp})
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps({"accounts": accounts}, indent=2))
    CFG.chmod(0o600)
    print(f"wrote {len(accounts)} account(s) to {CFG}")

    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "--now", "proton-bridge.service"])
    print("Bridge service started. Test:  uv run python -c 'import server; print(server.list_messages(limit=3))'")


if __name__ == "__main__":
    main()
