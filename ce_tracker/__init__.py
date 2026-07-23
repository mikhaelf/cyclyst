import os

from flask import Flask

from .db import init_db, seed_db


def create_app():
    app = Flask(__name__)

    init_db()
    # Demo data is opt-in: a fresh install starts empty (add credentials via the UI).
    # seed_db() itself refuses to touch a database that already has credentials.
    if os.environ.get("CE_TRACKER_DEMO") == "1":
        seed_db()

    from .routes import bp

    app.register_blueprint(bp)

    return app
