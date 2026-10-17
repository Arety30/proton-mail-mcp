#!/usr/bin/env bash
# One-time setup: log Proton accounts into Bridge (headless) and store the Bridge
# passwords for the MCP server. Re-run to add more accounts.
set -euo pipefail
BRIDGE=/usr/lib/protonmail/bridge/bridge
CFG=~/.config/proton-mail-mcp/accounts.json
mkdir -p "$(dirname "$CFG")"

# Bridge stores its vault in the system keyring; on headless/CLI runs make sure
# a keyring is unlocked (GNOME Keyring on this laptop is fine).
echo "== Proton Mail Bridge headless CLI =="
echo "Inside the CLI type:  login   (email, password, 2FA, mailbox password if any)"
echo "Repeat 'login' for each of your Proton accounts, then type:  info"
echo "'info' prints the *Bridge password* for each account — copy it, then type: exit"
echo
systemctl --user stop proton-bridge.service 2>/dev/null || true
"$BRIDGE" --cli

echo
echo "== Now enter the accounts for the MCP server =="
python3 - "$CFG" <<'PY'
import json, sys, getpass, pathlib
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text()) if p.exists() else {"accounts": []}
while True:
    email = input("Proton email (blank to finish): ").strip()
    if not email: break
    name = input(f"  short name [{email.split('@')[0]}]: ").strip() or email.split('@')[0]
    pw = getpass.getpass("  Bridge password (from 'info'): ").strip()
    data["accounts"] = [a for a in data["accounts"] if a["email"] != email] + [{"name": name, "email": email, "password": pw}]
p.write_text(json.dumps(data, indent=2)); p.chmod(0o600)
print(f"saved {len(data['accounts'])} account(s) to {p}")
PY

systemctl --user daemon-reload
systemctl --user enable --now proton-bridge.service
sleep 4
echo; echo "== smoke test =="
cd "$(dirname "$0")" && uv run python -c "import server; print(server.list_accounts()); print(server.list_messages(limit=3))"
