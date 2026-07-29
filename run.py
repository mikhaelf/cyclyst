import os

from ce_tracker import create_app

app = create_app()

if __name__ == "__main__":
    # Off by default: Werkzeug's debug mode serves an interactive Python console on any
    # unhandled exception — fine for local iteration, arbitrary code execution if this ever
    # ends up reachable beyond localhost. Opt in per session with CE_TRACKER_DEBUG=1.
    app.run(debug=os.environ.get("CE_TRACKER_DEBUG") == "1")
