"""Tests for the Host-header allowlist in ce_tracker.create_app (the DNS-rebinding defense).
No database is touched: create_app's database/secret-key helpers are patched out, and the
requests only hit Flask's static-file route. Run with:
    python -m unittest discover -s tests
"""

import os
import unittest
from contextlib import ExitStack
from unittest import mock

import ce_tracker

STATIC_URL = "/static/style.css"


def _make_app(env_trusted_hosts=None):
    with ExitStack() as stack:
        # patch.dict restores the real environment on exit, whatever is changed inside.
        stack.enter_context(mock.patch.dict(os.environ))
        os.environ.pop("CE_TRACKER_DEMO", None)
        os.environ.pop("CE_TRACKER_TRUSTED_HOSTS", None)
        if env_trusted_hosts is not None:
            os.environ["CE_TRACKER_TRUSTED_HOSTS"] = env_trusted_hosts
        stack.enter_context(
            mock.patch.object(ce_tracker, "get_or_create_secret_key", return_value="test-key")
        )
        stack.enter_context(mock.patch.object(ce_tracker, "init_db"))
        stack.enter_context(mock.patch.object(ce_tracker, "seed_db"))
        stack.enter_context(mock.patch.object(ce_tracker, "get_db"))
        stack.enter_context(mock.patch.object(ce_tracker.run_summary, "record_app_start"))
        return ce_tracker.create_app()


def _status(app, host):
    with app.test_client() as client:
        resp = client.get(STATIC_URL, base_url=f"http://{host}")
        try:
            return resp.status_code
        finally:
            resp.close()


class TrustedHostsTests(unittest.TestCase):
    def test_default_local_hosts_are_served(self):
        app = _make_app()
        for host in ("127.0.0.1:5000", "localhost:5000", "127.0.0.1", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(_status(app, host), 200)

    def test_rebound_hostname_is_rejected(self):
        # A DNS-rebinding page reaches 127.0.0.1 but the browser still sends its own hostname.
        app = _make_app()
        for host in ("attacker.example:5000", "attacker.example", "127.0.0.1.attacker.example:5000"):
            with self.subTest(host=host):
                self.assertEqual(_status(app, host), 400)

    def test_env_var_adds_hosts_without_dropping_defaults(self):
        app = _make_app(" tracker.lan , ,192.168.1.20 ")
        self.assertEqual(
            app.config["TRUSTED_HOSTS"], ["127.0.0.1", "localhost", "tracker.lan", "192.168.1.20"]
        )
        self.assertEqual(_status(app, "tracker.lan:5000"), 200)
        self.assertEqual(_status(app, "192.168.1.20:5000"), 200)
        self.assertEqual(_status(app, "localhost:5000"), 200)
        self.assertEqual(_status(app, "attacker.example:5000"), 400)

    def test_blank_env_var_never_yields_trust_all(self):
        # Flask treats an empty TRUSTED_HOSTS list as "trust every host".
        for value in ("", "   ", ",", " , ,"):
            with self.subTest(value=value):
                app = _make_app(value)
                self.assertEqual(app.config["TRUSTED_HOSTS"], ["127.0.0.1", "localhost"])
                self.assertEqual(_status(app, "attacker.example:5000"), 400)


if __name__ == "__main__":
    unittest.main()
