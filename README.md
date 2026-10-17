# proton-mail-mcp

proton-mail-mcp is a Model Context Protocol (MCP) server by Syed Abrar Ali that lets any MCP client read, search, send and organise email across several mailboxes at once. Proton Mail does not offer IMAP/SMTP directly, so the server talks to a local bridge instead: either the official Proton Mail Bridge or the open-source third-party bridge hydroxide, which also works with free Proton plans. Gmail and any generic IMAP/SMTP provider are supported too. Alongside the server, the repository contains the helper scripts that get accounts logged into a bridge without typing into an interactive CLI, including a small WebKitGTK window that solves Proton's human-verification CAPTCHA the way Proton's own mobile app does. The result is one `accounts.json` file and one server that gives an agent eleven mail tools over every configured account.

## Where it is used

In Abrar's setup this server is the single mail connector for his agentic tools. It runs over stdio for desktop MCP clients and, as a user service, over streamable HTTP with bearer-token auth for remote clients that can only reach a public URL. The same `accounts.json` it maintains is read by other projects that need to send mail through the local bridge (for example password-reset mail from his chat site), so logging an account in once here makes it available everywhere.

## How it works

### Server (`server.py`)

- Built on `mcp.server.fastmcp.FastMCP`. Every tool takes an optional `account` argument (short name or address; defaults to the first account) so one process serves many mailboxes.
- Accounts are loaded fresh from `~/.config/proton-mail-mcp/accounts.json` on every call, because bridge passwords change when an account re-authenticates. Each account entry picks a provider preset, optionally overriding any field:

| Provider | IMAP | SMTP | Notes |
| --- | --- | --- | --- |
| `proton` (default) | `127.0.0.1:1143` STARTTLS | `127.0.0.1:1025` STARTTLS | official Proton Mail Bridge; verifies the bridge's own certificate from `~/.config/protonmail/bridge-v3/cert.pem` when present |
| `hydroxide` | `127.0.0.1:1144` plain | `127.0.0.1:1026` plain | open-source bridge, loopback only |
| `gmail` | `imap.gmail.com:993` SSL | `smtp.gmail.com:465` SSL | App Password; Gmail folder names such as `[Gmail]/Trash` |
| `generic` | `:993` SSL | `:465` SSL | supply `imap_host` / `smtp_host` yourself |

  An optional `login` field is the IMAP/SMTP username when it differs from the From address (custom-domain aliases).
- IMAP access goes through a small `_Imap` context manager that selects a folder (read-only unless a tool must change flags) and always logs out. Message lists fetch only `FLAGS` and the From/To/Subject/Date headers for the last N UIDs; `read_message` fetches the full RFC822 body, prefers the `text/plain` part and falls back to HTML stripped to text, truncating at `max_chars`.
- Tools: `list_accounts`, `list_folders` (with message/unread counts), `list_messages`, `search_messages` (IMAP `SUBJECT OR BODY`, plus `FROM` and `SINCE` filters), `read_message`, `send_email` (comma-separated to/cc/bcc, optional HTML with an automatic plain-text alternative, `In-Reply-To`/`References` threading), `reply_to_message` (reply or reply-all, `Re:` prefix), `mark_message` (seen/flagged), `move_message` (IMAP `MOVE`, falling back to copy + delete + expunge), `delete_message` (move to the provider's Trash), `save_draft` (IMAP `APPEND` with the `\Draft` flag).
- Transports: `python server.py` speaks stdio. `python server.py --http [--host] [--port]` serves streamable HTTP at `/mcp` behind a Starlette middleware that requires `Authorization: Bearer $MCP_TOKEN`; `MCP_NOAUTH=1` disables the check for setups where the secret is in an unguessable URL path. DNS-rebinding protection is turned off because the public hostname is supplied by a proxy in front of the server.

### Login helpers

| File | Role |
| --- | --- |
| `setup.sh` | Interactive one-time path for the official bridge: opens `bridge --cli`, you type `login`/`info`/`exit`, then the script prompts for each address and bridge password, writes `accounts.json` (mode 600), enables the `proton-bridge` user service and runs a smoke test |
| `auto_login.py` | Non-interactive version: reads `PROTON_n_*` from `~/.config/proton-mail-mcp/proton.env`, drives the bridge CLI with `pexpect`, answers 2FA prompts with `pyotp` TOTP codes, parses the `info` output for bridge passwords, writes `accounts.json`, starts the service |
| `hydroxide_login.py` | Same idea for hydroxide: runs `hydroxide auth <address>`, handles password/2FA prompts, and when the (patched) hydroxide prints a `HUMAN_VERIFICATION_TOKEN`, launches `captcha_webview.py`, then retries with `HYDROXIDE_HV_TOKEN`/`HYDROXIDE_HV_TYPE` set. Registers accounts with `"provider": "hydroxide"` and restarts the `hydroxide` user service |
| `captcha_webview.py` | Opens Proton's embedded verification page in a WebKitGTK window with an injected `AndroidInterface.dispatch` object (mirroring Proton's Android HV dialog); when the page posts `HUMAN_VERIFICATION_SUCCESS` it prints the solved token as JSON and exits |
| `login_web.py` | Tiny local HTTP page (default port 55) that shows configured accounts, whether the bridge port answers, and a form to add/remove Proton or Gmail accounts by hand |
| `apply_gmail.py` | Registers `GMAIL_n_*` accounts from `proton.env` with `"provider": "gmail"`, tests each with `list_messages`, then calls `set_relay.sh` with the first one |
| `set_relay.sh` | Points a local Postfix at Gmail's submission port with SASL (for a self-hosted mailbox whose ISP blocks outbound port 25) |

### Storage

Everything persistent lives under `~/.config/proton-mail-mcp/`: `accounts.json` (names, addresses, bridge/app passwords, provider), `proton.env` (login credentials and TOTP secrets for the helper scripts), and `http.env` (the bearer token for HTTP mode). All are created with mode 600. The server itself keeps no state.

## Setup

Requirements: Python 3.12+, [uv](https://github.com/astral-sh/uv), and one of: Proton Mail Bridge (paid plans), hydroxide built to `~/go/bin/hydroxide` (free plans; the CAPTCHA flow needs the patched build in `hydroxide-src/` plus `python3-gi` with WebKit2GTK 4.1), or a Gmail App Password. Python dependencies (`mcp[cli]`, `pexpect`, `pyotp`, `starlette`, `uvicorn`) come from `pyproject.toml`.

```bash
cd ~/proton-mail-mcp
uv sync
mkdir -p ~/.config/proton-mail-mcp && chmod 700 ~/.config/proton-mail-mcp
```

Credentials file `~/.config/proton-mail-mcp/proton.env` (mode 600), one block per account:

```
PROTON_1_NAME=personal
PROTON_1_EMAIL=user@example.com
PROTON_1_PASSWORD=your-proton-password
PROTON_1_TOTP_SECRET=BASE32SECRET      # blank if 2FA is off
PROTON_1_MAILBOX_PASSWORD=             # only for two-password accounts
GMAIL_1_NAME=gmail
GMAIL_1_EMAIL=user@example.com
GMAIL_1_PASSWORD=xxxx xxxx xxxx xxxx    # 16-character App Password
```

Then log accounts in with one of:

```bash
uv run python hydroxide_login.py          # free or paid Proton via hydroxide (solve one CAPTCHA per account)
uv run python auto_login.py               # official bridge, non-interactive
./setup.sh                                # official bridge, interactive
uv run python apply_gmail.py              # Gmail accounts
```

Or write `accounts.json` yourself:

```json
{"accounts": [
  {"name": "personal", "email": "user@example.com", "password": "<bridge password>", "provider": "hydroxide"},
  {"name": "gmail",    "email": "user@example.com", "password": "<app password>",    "provider": "gmail"}
]}
```

Environment variables for the server: `PROTON_MCP_CONFIG` (alternative accounts file path), `MCP_TOKEN` (required in `--http` mode), `MCP_NOAUTH=1` (skip bearer auth).

## Usage

stdio for a desktop MCP client (add to the client's MCP config):

```json
{"mcpServers": {"proton-mail": {"command": "uv", "args": ["run", "--directory", "~/proton-mail-mcp", "python", "server.py"]}}}
```

HTTP for remote clients:

```bash
MCP_TOKEN=change-me uv run python server.py --http --port 8973
# client URL: http://HOST:8973/mcp with header  Authorization: Bearer change-me
```

Quick checks without a client:

```bash
uv run mcp dev server.py                                           # MCP inspector
uv run python -c "import server; print(server.list_accounts())"
uv run python -c "import server; print(server.list_messages('personal', limit=5, unread_only=True))"
uv run python -c "import server; print(server.search_messages('invoice', sender='billing', since='2026-01-01'))"
uv run python -c "import server; print(server.send_email('user@example.com', 'Hi', 'Hello from the MCP', account='gmail'))"
```

Typical agent interaction: call `list_accounts`, then `list_messages(account="personal", unread_only=True)`, `read_message(uid="1234")`, and `reply_to_message(uid="1234", body="Thanks, received.")`.

## Project structure

```
proton-mail-mcp/
├── server.py            # FastMCP server: providers, IMAP/SMTP helpers, 11 tools, stdio/HTTP entrypoint
├── auto_login.py        # official bridge login via pexpect + TOTP, writes accounts.json
├── hydroxide_login.py   # hydroxide login with CAPTCHA hand-off
├── captcha_webview.py   # WebKitGTK window that returns Proton's human-verification token
├── login_web.py         # local web page to view/add/remove accounts
├── apply_gmail.py       # register Gmail accounts and set the Postfix relay
├── set_relay.sh         # Postfix -> Gmail submission relay
├── setup.sh             # interactive first-time setup for the official bridge
├── pyproject.toml / uv.lock
└── hydroxide-src/       # patched hydroxide source (built separately, not documented here)
```

## Limitations and notes

- The official Proton Mail Bridge only works with paid Proton plans; hydroxide is the route for free plans and is a third-party project, so Proton API changes can break it. Proton answers headless logins with a CAPTCHA almost every time, which is why the WebKitGTK solver exists; it needs a graphical session.
- Bridge passwords rotate when an account re-authenticates, so `accounts.json` must be rewritten (re-run the login script); the server re-reads the file on every call so no restart is needed.
- All bridge traffic is plain or self-signed TLS on loopback; do not expose the bridge ports. Only the MCP HTTP port is meant to be proxied, and only with a token.
- `search_messages` relies on server-side IMAP `SEARCH`; results and body matching depend on the bridge's search implementation. List and search are capped at 200 messages per call.
- Attachments are reported by name only; there is no download or attach support.
- `reply_to_message` quotes nothing from the original; the reply body must be complete.
- `set_relay.sh` and the Postfix relay are specific to a self-hosted mailbox and require sudo; they are optional.
- Anyone holding `MCP_TOKEN` (or reaching a `MCP_NOAUTH=1` endpoint) can read and send mail from every configured account.
