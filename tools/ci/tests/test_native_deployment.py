"""Deployment preflight rejects unusable inputs without exposing credentials."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[3] / "tools/docker/riscv64/preflight.py"


class NativeDeploymentTests(unittest.TestCase):
    def load(self):
        self.assertTrue(SCRIPT.is_file(), "deployment preflight is missing")
        spec = importlib.util.spec_from_file_location("deployment_preflight", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_rejects_missing_or_container_local_backend(self):
        module = self.load()
        for url in (
            "",
            "http://localhost:8000/v1",
            "http://127.0.0.1/v1",
            "http://[::1]/v1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.validate_backend(url, "model")
        with self.assertRaises(ValueError):
            module.validate_backend("https://inference.example/v1", " ")

    def test_rejects_embedded_credentials_and_malformed_urls(self):
        module = self.load()
        for url in (
            "https://user:secret@inference.example/v1",
            "https://inference.example/v1?api_key=secret",
            "https://inference.example/v1#secret",
            "file:///v1",
            "http://inference.example:bad/v1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError) as error:
                module.validate_backend(url, "model")
            self.assertNotIn("secret", str(error.exception))

    def test_accepts_reachable_address_shape_without_contacting_backend(self):
        self.load().validate_backend("https://inference.example/v1", "org/model")

    def test_rejects_wrong_daemon_or_image_architecture(self):
        module = self.load()
        for output in ("linux/x86_64", "linux/arm64", "windows/riscv64"):
            with (
                patch.object(module, "docker_output", return_value=output),
                self.assertRaises(ValueError),
            ):
                module.check_docker("router:test")
        with (
            patch.object(
                module, "docker_output", side_effect=["linux/riscv64", "linux/amd64"]
            ),
            self.assertRaises(ValueError),
        ):
            module.check_docker("router:test")
        with patch.object(
            module, "docker_output", side_effect=["linux/riscv64", "linux/riscv64"]
        ):
            module.check_docker("router:test")

    def test_cli_failure_does_not_echo_backend_credentials(self):
        self.load()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text(
                "providers:\n  models:\n    - provider_model_id: model\n      backend_refs:\n        - base_url: https://user:secret@example/v1\n"
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--config", str(config)],
                env=os.environ,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("secret", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
