"""
Entry point for the local browser control panel.

    python3 webui/server.py                 # http://127.0.0.1:8765
    python3 webui/server.py --port 9000
    python3 webui/server.py --host 0.0.0.0  # LAN — no auth is built, see below

Bind defaults come from config.WEB_UI_HOST / WEB_UI_PORT; env
FAUXWORKING_UI_HOST / FAUXWORKING_UI_PORT override those; a --host/--port flag
wins over everything. Localhost is the intended mode — the panel can trigger
teardown and the process holds Nexudus tokens, so binding it to a routable
address is a deliberate, at-your-own-risk choice and prints a warning.
"""

from __future__ import annotations

import argparse
import os
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from webui.handler import Handler
from webui.jobs import JobManager


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Fauxworking browser control panel")
    parser.add_argument("--host", default=os.environ.get("FAUXWORKING_UI_HOST", config.WEB_UI_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("FAUXWORKING_UI_PORT", config.WEB_UI_PORT)))
    return parser.parse_args(argv)


def build_server(host, port):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.manager = JobManager()
    httpd.ui_host = host
    httpd.ui_port = port
    return httpd


def main(argv=None):
    args = _parse_args(argv)
    httpd = build_server(args.host, args.port)

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"Fauxworking control panel: http://{shown_host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("!  Bound to a non-localhost address. There is no panel auth — anyone who can "
              "reach this port can seed or tear down the live account. Only do this on a "
              "network you trust.")
    print("   Ctrl-C to stop.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        active = httpd.manager.active()
        if active is not None:
            print(f"   Cancelling the in-progress run ({active.run_id})...")
            try:
                httpd.manager.cancel(active.run_id)
            except Exception:  # noqa: BLE001
                pass
        httpd.shutdown()
    httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
