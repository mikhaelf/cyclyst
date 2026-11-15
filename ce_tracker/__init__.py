import os

from flask import Flask
from flask_wtf import CSRFProtect

from . import run_summary
from .db import get_db, get_or_create_secret_key, init_db, seed_db

# Bounds request body size (enforced by Werkzeug before the body is fully buffered) — a
# defense against memory/CPU exhaustion from an oversized PDF handed to the upload-parsing
# routes. 25 MB is generous for a single certificate (typically under 1 MB) and for
# import_transcripts' multi-file bulk-import batch, while still bounding the worst case.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Hostnames this server answers to; a request with any other Host header gets a 400 (Flask's
# TRUSTED_HOSTS check). The app has no login and relies on being reachable only from this
# machine, but binding to 127.0.0.1 alone doesn't stop DNS rebinding: a malicious website can
# re-point its own domain at 127.0.0.1, after which the browser treats this dashboard as
# same-origin with that site and lets its scripts read every page (CSRF tokens included) and
# submit forms. Those requests still carry the attacker's hostname, so checking Host blocks them.
DEFAULT_TRUSTED_HOSTS = ["127.0.0.1", "localhost"]


def trusted_hosts():
    # CE_TRACKER_TRUSTED_HOSTS (comma-separated) adds names for setups reached some other way,
    # e.g. a LAN hostname. It only ever extends the defaults, never replaces them: Flask treats
    # an empty TRUSTED_HOSTS list as "trust every host", so a blank or malformed value must not
    # be able to produce one.
    extra = os.environ.get("CE_TRACKER_TRUSTED_HOSTS", "")
    return DEFAULT_TRUSTED_HOSTS + [h.strip() for h in extra.split(",") if h.strip()]


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_or_create_secret_key()
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["TRUSTED_HOSTS"] = trusted_hosts()
    CSRFProtect(app)

    init_db()
    # Demo data is opt-in: a fresh install starts empty (add credentials via the UI).
    # seed_db() itself refuses to touch a database that already has credentials.
    if os.environ.get("CE_TRACKER_DEMO") == "1":
        seed_db()

    conn = get_db()
    run_summary.record_app_start(conn)
    conn.close()

    from .routes import bp

    app.register_blueprint(bp)

    return app
