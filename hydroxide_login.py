#!/usr/bin/env python3
"""Log Proton accounts into hydroxide (open-source bridge, works with free plans) and register them
for the MCP.  Reads PROTON_n_* from proton.env.

    uv run python hydroxide_login.py                 # every account not yet registered as hydroxide
    uv run python hydroxide_login.py user@example.com [...]   # only these (re-auths even if registered)

When Proton demands a CAPTCHA (code 9001) a small window opens (captcha_webview.py); solve it and the
login continues automatically with the human-verification token, exactly like Proton's own apps.
"""
import json, os, re, subprocess, sys
from pathlib import Path
import pexpect
sys.path.insert(0, str(Path(__file__).parent))
from auto_login import load_env, ENV, CFG, totp

HYD = Path("~/go/bin/hydroxide").expanduser()
HERE = Path(__file__).parent


def hydroxide_auth(a: dict, hv: dict | None = None) -> tuple[str, str | None, str]:
    """Returns (status, bridge_password, raw_output). status: ok | captcha:<hvtoken> | error"""
    env = dict(os.environ)
    if hv:
        env["HYDROXIDE_HV_TOKEN"], env["HYDROXIDE_HV_TYPE"] = hv["token"], hv.get("type", "captcha")
    c = pexpect.spawn(str(HYD), ["auth", a["email"]], encoding="utf-8", timeout=180, env=env)
    c.expect(r"(?i)password"); c.sendline(a["password"])
    while True:
        i = c.expect([r"(?i)two.?factor|2fa code", r"Bridge password:\s*(\S+)", r"HUMAN_VERIFICATION_TOKEN=(\S+)", pexpect.EOF])
        if i == 0:
            if not a["totp"]:
                return "error", None, "2FA required — set PROTON_n_TOTP_SECRET"
            c.sendline(totp(a["totp"]))
        elif i == 1:
            bp = c.match.group(1); c.expect(pexpect.EOF, timeout=30)
            return "ok", bp, c.before
        elif i == 2:
            tok = c.match.group(1); c.expect(pexpect.EOF, timeout=30)
            return f"captcha:{tok}", None, c.before
        else:
            return "error", None, c.before


def solve_captcha(hv_token: str) -> dict | None:
    print("  Proton wants a CAPTCHA — solve it in the window that just opened…")
    r = subprocess.run(["/usr/bin/python3", str(HERE / "captcha_webview.py"), hv_token], capture_output=True, text=True)
    if r.returncode != 0:
        print("  captcha window:", (r.stderr or r.stdout).strip()[-200:]); return None
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    cfg = json.loads(CFG.read_text()) if CFG.exists() else {"accounts": []}
    only = [e.lower() for e in sys.argv[1:]]
    have = {a["email"].lower() for a in cfg["accounts"] if a.get("provider") == "hydroxide"}
    for a in load_env():
        e = a["email"].lower()
        if not a["password"] or (only and e not in only) or (not only and e in have):
            continue
        print(f"hydroxide auth {a['email']} …")
        status, bp, out = hydroxide_auth(a)
        if status.startswith("captcha:"):
            hv = solve_captcha(status.split(":", 1)[1])
            if hv:
                status, bp, out = hydroxide_auth(a, hv)
        if status == "ok":
            cfg["accounts"] = [x for x in cfg["accounts"] if x["email"].lower() != e]
            cfg["accounts"].append({"name": a["name"], "email": a["email"], "password": bp, "provider": "hydroxide"})
            print(f"  OK — registered {a['email']} (hydroxide)")
        else:
            err = re.findall(r"\[\d+\][^\n]*", out or "")
            print(f"  FAILED: {err[-1] if err else (out or status).strip()[-200:]}")
    CFG.write_text(json.dumps(cfg, indent=2)); CFG.chmod(0o600)
    subprocess.run(["systemctl", "--user", "restart", "hydroxide.service"])
    print("accounts:", [x["name"] for x in cfg["accounts"]])


if __name__ == "__main__":
    main()
