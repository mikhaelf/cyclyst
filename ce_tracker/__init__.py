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


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_or_create_secret_key()
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
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
