#!/usr/bin/env -S /usr/bin/python3
"""Solve a Proton human-verification CAPTCHA the way Proton's own Android app does.

    captcha_webview.py <HV token>   -> prints the solved token on stdout

Loads https://verify.proton.me/?embed=true&token=<HV>&methods=captcha in a WebKitGTK window with an
`AndroidInterface.dispatch(json)` object injected before the page runs (mirrors
protoncore_android HV3DialogFragment). The page calls it with
{"type":"HUMAN_VERIFICATION_SUCCESS","payload":{"token":"...","type":"captcha"}}.
"""
import json
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2  # noqa: E402

if len(sys.argv) < 2:
    sys.exit("usage: captcha_webview.py <human verification token>")
hv = sys.argv[1]
url = f"https://verify.proton.me/?embed=true&token={hv}&methods=captcha&theme=2"

INJECT = """
window.AndroidInterface = { dispatch: function (s) { window.webkit.messageHandlers.hv.postMessage(String(s)); } };
window.addEventListener('message', function (e) {
  try { window.webkit.messageHandlers.hv.postMessage(typeof e.data === 'string' ? e.data : JSON.stringify(e.data)); } catch (_) {}
});
"""

result = {}


def on_msg(_mgr, js_result):
    raw = js_result.get_js_value().to_string()
    try:
        m = json.loads(raw)
    except Exception:
        return
    if m.get("type") == "HUMAN_VERIFICATION_SUCCESS":
        result["token"] = m["payload"]["token"]
        result["type"] = m["payload"].get("type", "captcha")
        Gtk.main_quit()
    elif m.get("type") == "NOTIFICATION":
        print("proton:", m.get("payload", {}).get("text"), file=sys.stderr)


ucm = WebKit2.UserContentManager()
ucm.register_script_message_handler("hv")
ucm.connect("script-message-received::hv", on_msg)
ucm.add_script(WebKit2.UserScript(INJECT, WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                                  WebKit2.UserScriptInjectionTime.START, None, None))
web = WebKit2.WebView(user_content_manager=ucm)
web.get_settings().set_enable_javascript(True)
web.get_settings().set_user_agent("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0")

win = Gtk.Window(title="Proton CAPTCHA — solve it to continue")
win.set_default_size(520, 640)
win.connect("destroy", Gtk.main_quit)
win.add(web)
win.show_all()
web.load_uri(url)
Gtk.main()

if "token" not in result:
    sys.exit("captcha window closed without a token")
print(json.dumps(result))
