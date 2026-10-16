"""The native dependency builder must reject unverified source before building."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/docker/build-valkey-glide-riscv64.sh"


class RiscvGlideBuildTests(unittest.TestCase):
    def test_bad_archive_cannot_produce_a_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "wrong.tar.gz"
            archive.write_bytes(b"not the pinned GLIDE source")
            output = root / "output"
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--archive",
                    str(archive),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source checksum mismatch", result.stderr)
            self.assertFalse(output.exists())

    def test_missing_archive_is_not_silently_downloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--archive",
                    str(root / "missing.tar.gz"),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source archive not found", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
