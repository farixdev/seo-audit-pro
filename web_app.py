# -*- coding: utf-8 -*-
"""
SEO Audit Pro — API server entry point.

Development:   python web_app.py            (http://127.0.0.1:5055)
Production:    gunicorn web_app:app --workers 1 --threads 32 --timeout 180

Run exactly ONE server process: the realtime hub, the audit worker pool and
the email queue live in-process.
"""

import os

from backend.app import create_app

app = create_app()


def start_server(port=None, host=None):
    port = int(os.environ.get("PORT") or port or 5055)
    host = host or os.environ.get("HOST") or "127.0.0.1"
    print(f"SEO Audit Pro API listening on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    start_server()
