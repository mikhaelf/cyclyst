from flask import Flask

from .db import init_db, seed_db


def create_app():
    app = Flask(__name__)

    init_db()
    seed_db()

    from .routes import bp

    app.register_blueprint(bp)

    return app
