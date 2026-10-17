"""The public build plan cannot accidentally reuse private SDK caches."""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/docker/riscv64/build.py"


class NativeBuildPlanTests(unittest.TestCase):
    def test_native_platform_is_explicit_for_dynamic_stage_resolution(self):
        for command in self.plan()["commands"]:
            self.assertIn("TARGETARCH=riscv64", command)
            self.assertIn("TARGETOS=linux", command)
            self.assertIn("BUILDPLATFORM=linux/riscv64", command)

    def test_engine_local_sdk_requires_the_docker_driver(self):
        spec = importlib.util.spec_from_file_location("native_build", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate_builder(
            "Name: default\nDriver: docker\nPlatforms: linux/riscv64\n"
        )
        with self.assertRaisesRegex(ValueError, "docker driver"):
            module.validate_builder("Name: custom\nDriver: docker-container\n")

    def plan(self, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--plan", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_router_build_first_constructs_sdk_without_cache_imports(self):
        commands = self.plan()["commands"]
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertIn("--no-cache", command)
            self.assertNotIn("--cache-from", command)
        self.assertIn("tools/docker/riscv64/Dockerfile.toolchain", commands[0])
        self.assertIn("vllm-sr-attached", commands[1])
        self.assertIn("GO_IMAGE=semantic-router-native-sdk:public", commands[1])
        self.assertIn("RUST_IMAGE=semantic-router-native-sdk:public", commands[1])

    def test_operator_uses_same_public_sdk_and_pinned_runtime(self):
        commands = self.plan("--component", "operator")["commands"]
        self.assertIn("deploy/operator/Dockerfile", commands[-1])
        self.assertIn("GO_IMAGE=semantic-router-native-sdk:public", commands[-1])
        runtime = next(x for x in commands[-1] if x.startswith("RUNTIME_IMAGE="))
        self.assertIn("@sha256:", runtime)

    def test_base_mirror_override_applies_to_all_router_runtime_inputs(self):
        base = "mirror.example/debian@sha256:" + "a" * 64
        commands = self.plan("--base-image", base)["commands"]
        self.assertIn("BASE_IMAGE=" + base, commands[0])
        self.assertIn("ATTACHED_BASE_IMAGE=" + base, commands[1])
        self.assertIn("ASSETS_IMAGE=" + base, commands[1])

    def test_dashboard_requires_explicit_static_assets(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--plan", "--component", "dashboard"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--frontend-assets", result.stderr)

    def test_mutable_base_is_rejected(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--plan", "--base-image", "debian:latest"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("digest", result.stderr)


if __name__ == "__main__":
    unittest.main()
