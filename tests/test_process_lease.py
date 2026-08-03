#!/usr/bin/env python3
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from app.core.process_lease import FileLease


class FileLeaseTests(unittest.TestCase):
    def test_only_one_live_owner_can_acquire_a_job_lease(self):
        with tempfile.TemporaryDirectory(prefix="niuone-lease-") as temp_dir:
            path = Path(temp_dir) / "scan.lock"
            first = FileLease(path, stale_after_seconds=60)
            second = FileLease(path, stale_after_seconds=60)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_stale_lease_is_recovered_and_old_owner_cannot_remove_new_lease(self):
        with tempfile.TemporaryDirectory(prefix="niuone-lease-") as temp_dir:
            path = Path(temp_dir) / "scan.lock"
            old = FileLease(path, stale_after_seconds=1)
            self.assertTrue(old.acquire())
            old_time = time.time() - 10
            os.utime(path, (old_time, old_time))

            replacement = FileLease(path, stale_after_seconds=1)
            self.assertTrue(replacement.acquire())
            old.release()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["token"], replacement.token)

            replacement.release()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
