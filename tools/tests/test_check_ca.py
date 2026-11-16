import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import check_ca  # noqa: E402


def _paths(cafile, capath):
    return SimpleNamespace(openssl_cafile_env="SSL_CERT_FILE", openssl_cafile=cafile,
                           openssl_capath_env="SSL_CERT_DIR", openssl_capath=capath)


class CaStatusTest(unittest.TestCase):
    def test_missing_cafile_and_capath(self):
        with tempfile.TemporaryDirectory() as d:
            cafile, ok = check_ca.ca_status(_paths(os.path.join(d, "cert.pem"), os.path.join(d, "certs")), {})
        self.assertFalse(ok)
        self.assertTrue(cafile.endswith("cert.pem"))

    def test_existing_cafile(self):
        with tempfile.NamedTemporaryFile(suffix=".pem") as f:
            _, ok = check_ca.ca_status(_paths(f.name, "/nonexistent"), {})
        self.assertTrue(ok)

    def test_empty_capath_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            _, ok = check_ca.ca_status(_paths("/nonexistent/cert.pem", d), {})
        self.assertFalse(ok)

    def test_env_override_wins(self):
        with tempfile.NamedTemporaryFile(suffix=".pem") as f:
            cafile, ok = check_ca.ca_status(_paths("/nonexistent/cert.pem", "/nonexistent"),
                                            {"SSL_CERT_FILE": f.name})
            self.assertEqual(cafile, f.name)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
