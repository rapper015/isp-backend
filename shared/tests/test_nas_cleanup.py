"""Exercise NAS failure cleanup without a router or database connection."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class NasCleanupTests(unittest.TestCase):
    def test_adapter_creation_and_disconnect_failures_release_lock(self):
        root = Path(__file__).resolve().parents[2]
        environment = dict(os.environ, DATABASE_URL="sqlite:///:memory:", SENTRY_DSN="",
            PYTHONPATH=os.pathsep.join((str(root / "services/aaa-service"), str(root / "shared/python"))))
        code = '''
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from app import workers
for fail_build in (True, False):
    job = SimpleNamespace(id="job", nas_id="nas", status="QUEUED", safe_result={},
                          correlation_id="request", idempotency_key="key", job_type="connection_test")
    nas = SimpleNamespace(id="nas", enabled=True, tenant_id="tenant", lifecycle_status="REGISTERED")
    session = MagicMock()
    session.get.side_effect = [job, nas, job]
    adapter = MagicMock()
    adapter.disconnect.side_effect = RuntimeError("disconnect failed")
    with patch.object(workers, "acquire_nas_lock", return_value=(True, "owner")), \\
         patch.object(workers, "release_nas_lock") as release, \\
         patch.object(workers, "build_adapter", side_effect=RuntimeError("build failed") if fail_build else None, return_value=adapter), \\
         patch.object(workers, "_dispatch_job", return_value={"status": "FAILED", "event": "test.failed", "error": "TEST_FAILURE"}):
        assert workers.process_nas_job(session, "job") == "FAILED"
        release.assert_called_once_with(session, "nas", "owner")
'''
        result = subprocess.run([sys.executable, "-c", code], env=environment, cwd=root,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
