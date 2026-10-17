#!/usr/bin/env python3
"""Register GMAIL_n_* accounts from proton.env in the MCP, set GMAIL_1 as the mailbridge relay, test both."""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from auto_login import ENV, CFG
env = {}
for l in ENV.read_text().splitlines():
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
cfg = json.loads(CFG.read_text()) if CFG.exists() else {"accounts": []}
added = []
for i in range(1, 6):
    e, p = env.get(f"GMAIL_{i}_EMAIL"), env.get(f"GMAIL_{i}_PASSWORD", "").replace(" ", "")
    if not e or not p:
        continue
    cfg["accounts"] = [a for a in cfg["accounts"] if a["email"].lower() != e.lower()]
    cfg["accounts"].append({"name": env.get(f"GMAIL_{i}_NAME") or f"gmail{i}", "email": e, "password": p, "provider": "gmail"})
    added.append((e, p))
if not added:
    sys.exit(f"no GMAIL_n_PASSWORD set in {ENV}")
CFG.write_text(json.dumps(cfg, indent=2)); CFG.chmod(0o600)
import server
for e, _ in added:
    try:
        ms = server.list_messages(e, limit=2); print(f"OK {e}: {len(ms)} recent, first: {ms[0]['subject'][:50] if ms else '-'}")
    except Exception as ex:
        print(f"FAIL {e}: {ex}")
e, p = added[0]
subprocess.run([str(Path(__file__).parent / "set_relay.sh"), e, p], check=False)
