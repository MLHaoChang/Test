"""The FastAPI backend (plan 5.8, WP11): the same services `pg` uses, over HTTP on 127.0.0.1.

`app.create_app` builds the API for one data directory; `pg serve` (`cli/serve.py`) runs it.
Every response is JSON-safe as the CLI's own `--json` output is: money and quantities are decimal
strings, never floats (plan 3.3), and no path here places, routes or automates an order (spec 1.5).
"""
