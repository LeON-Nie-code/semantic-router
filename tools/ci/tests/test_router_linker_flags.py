"""Keep the Router Make entry compatible with Go's existing linker flags."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("go"), "Go toolchain required")
class RouterLinkerFlagTests(unittest.TestCase):
    def build_value(self, override=None, dev=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src/semantic-router"
            (source / "cmd").mkdir(parents=True)
            (source / "go.mod").write_text("module linker-fixture\n\ngo 1.23\n")
            (source / "cmd/main.go").write_text(
                'package main\nimport "fmt"\nvar buildValue = "default"\n'
                "func main() { fmt.Print(buildValue) }\n"
            )
            command = [
                "make",
                "-f",
                str(ROOT / "tools/make/build-run-test.mk"),
                "build-router",
                "LOG_TARGET=:",
            ]
            if override is not None:
                command.append("ROUTER_LDFLAGS=" + override)
            if dev:
                command.append("DEV=1")
            subprocess.run(
                command,
                cwd=root,
                env=os.environ
                | {
                    "GOFLAGS": "-ldflags=-X=main.buildValue=environment",
                    "GOWORK": "off",
                },
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return subprocess.check_output([str(root / "bin/router")], text=True)

    def test_unset_override_preserves_goflags(self):
        self.assertEqual(self.build_value(), "environment")

    def test_dev_build_preserves_goflags(self):
        self.assertEqual(self.build_value(dev=True), "environment")

    def test_explicit_override_reaches_linker(self):
        self.assertEqual(self.build_value("-X main.buildValue=explicit"), "explicit")


if __name__ == "__main__":
    unittest.main()
