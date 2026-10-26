"""Import each API in isolation; never run lifespan or connect to a database."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class ServiceBootstrapTests(unittest.TestCase):
    def test_every_service_installs_diagnostics(self):
        root = Path(__file__).resolve().parents[2]
        services = sorted((root / "services").glob("*/app/main.py"))
        self.assertEqual(len(services), 16)
        for main in services:
            service = main.parents[1]
            with self.subTest(service=service.name):
                environment = dict(os.environ, DATABASE_URL="sqlite:///:memory:", SENTRY_DSN="",
                    PYTHONPATH=os.pathsep.join((str(service), str(root / "shared/python"))))
                result = subprocess.run([sys.executable, "-c",
                    "from app.main import app; assert app.state.observability_installed; "
                    "assert any(m.cls.__name__ == 'RequestDiagnostics' for m in app.user_middleware)"],
                    env=environment, cwd=root, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
