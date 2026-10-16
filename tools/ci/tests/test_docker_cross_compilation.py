from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(REPO_ROOT / "tools/ci"))
from image_artifacts import DEFINITIONS, ROUTER_BUILDS, ROUTER_DOCKERFILE  # noqa: E402


def router_build_command() -> str:
    """The router stage's final RUN command, with line continuations joined."""
    text = (REPO_ROOT / ROUTER_DOCKERFILE).read_text().replace("\\\n", " ")
    stage = text.split(" AS router-build", 1)[1].split("\nFROM ", 1)[0]
    runs = re.findall(r"(?m)^RUN (.+)$", stage)
    return next(run for run in reversed(runs) if "go build" in run)


class DockerCrossCompilationTests(unittest.TestCase):
    def test_dashboard_plain_debian_frontend_installs_node_and_npm(self) -> None:
        text = (REPO_ROOT / "dashboard/backend/Dockerfile").read_text()
        stage = (
            text.replace("\\\n", " ")
            .split(" AS frontend-builder", 1)[1]
            .split("\nFROM ", 1)[0]
        )
        command = re.findall(r"(?m)^RUN (.+)$", stage)[0]
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp)
            for name, body in {
                "npm": "echo node-runtime-not-installed >&2; exit 77",
                "apt-get": 'printf "apt-get %s\\n" "$*"',
                "rm": "exit 0",
            }.items():
                executable = stubs / name
                executable.write_text(f"#!/bin/sh\n{body}\n")
                executable.chmod(0o755)
            result = subprocess.run(
                ["/bin/sh", "-e", "-c", command],
                env={**os.environ, "PATH": str(stubs)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "install -y --no-install-recommends nodejs npm ca-certificates",
                result.stdout,
            )

    def test_dashboard_debian_wasm_builder_reuses_installed_git(self) -> None:
        text = (REPO_ROOT / "dashboard/backend/Dockerfile").read_text()
        stage = (
            text.replace("\\\n", " ")
            .split(" AS wasm-builder", 1)[1]
            .split("\nFROM ", 1)[0]
        )
        command = re.findall(r"(?m)^RUN (.+)$", stage)[0]
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp)
            for name, body in {
                "git": "exit 0",
                "apk": "echo unexpected-alpine-package-manager >&2; exit 77",
            }.items():
                executable = stubs / name
                executable.write_text(f"#!/bin/sh\n{body}\n")
                executable.chmod(0o755)
            result = subprocess.run(
                ["sh", "-e", "-c", command],
                env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_dashboard_runtime_installs_python_venv_on_plain_debian(self) -> None:
        text = (REPO_ROOT / "dashboard/backend/Dockerfile").read_text()
        stage = text.replace("\\\n", " ").split("# Stage 3: Final runtime image", 1)[1]
        command = re.findall(r"(?m)^RUN (.+)$", stage)[0]
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp)
            for name, body in {
                "python3": "exit 1",
                "apt-get": 'printf "apt-get %s\\n" "$*"',
                "rm": "exit 0",
            }.items():
                executable = stubs / name
                executable.write_text(f"#!/bin/sh\n{body}\n")
                executable.chmod(0o755)
            result = subprocess.run(
                ["sh", "-e", "-c", command],
                env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "install -y --no-install-recommends python3 python3-venv", result.stdout
            )

    def test_attached_native_riscv_build_uses_native_c_compiler(self) -> None:
        text = (REPO_ROOT / "tools/docker/Dockerfile.router-attached").read_text()
        stage = (
            text.replace("\\\n", " ")
            .split(" AS router-build-riscv64", 1)[1]
            .split("\nFROM ", 1)[0]
        )
        commands = re.findall(r"(?m)^RUN (.+)$", stage)
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp)
            programs = {
                "dpkg": "echo riscv64",
                "apt-get": "echo unexpected-cross-toolchain-install >&2; exit 77",
                "go": 'printf "CC=%s GOARCH=%s CGO_ENABLED=%s\\n" "$CC" "$GOARCH" "$CGO_ENABLED"',
            }
            for name, body in programs.items():
                executable = stubs / name
                executable.write_text(f"#!/bin/sh\n{body}\n")
                executable.chmod(0o755)
            environment = {
                key: value for key, value in os.environ.items() if key != "CC"
            } | {"PATH": f"{stubs}:{os.environ['PATH']}"}
            result = subprocess.run(
                ["bash", "-e", "-c", "\n".join(commands)],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(), "CC=gcc GOARCH=riscv64 CGO_ENABLED=1"
            )

    def test_attached_installed_rust_target_does_not_require_rustup(self) -> None:
        text = (REPO_ROOT / "tools/docker/Dockerfile.router-attached").read_text()
        stage = (
            text.replace("\\\n", " ")
            .split(" AS glide-riscv64", 1)[1]
            .split("\nFROM ", 1)[0]
        )
        command = next(
            run
            for run in re.findall(r"(?m)^RUN (.+)$", stage)
            if "rustup target add" in run
        )
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp)
            library = stubs / "target-lib"
            library.mkdir()
            for name, body in {
                "rustc": f"echo '{library}'",
                "rustup": "echo unexpected-rustup-download >&2; exit 97",
            }.items():
                executable = stubs / name
                executable.write_text(f"#!/bin/sh\n{body}\n")
                executable.chmod(0o755)
            result = subprocess.run(
                ["bash", "-e", "-c", command],
                env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_router_cross_compiles_arm64_with_only_the_target_c_linker(self) -> None:
        command = router_build_command()
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp)
            (stubs / "dpkg").write_text("#!/bin/sh\necho amd64\n")
            (stubs / "go").write_text(
                '#!/bin/sh\necho "CC=${CC:-} GOARCH=$GOARCH CGO_ENABLED=$CGO_ENABLED"\n'
            )
            for stub in stubs.iterdir():
                stub.chmod(0o755)
            for arch, linker in (("arm64", "aarch64-linux-gnu-gcc"), ("amd64", "")):
                with self.subTest(arch=arch):
                    environment = {
                        key: value for key, value in os.environ.items() if key != "CC"
                    } | {"PATH": f"{stubs}:{os.environ['PATH']}", "TARGETARCH": arch}
                    result = subprocess.run(
                        ["bash", "-e", "-c", command],
                        env=environment,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        result.stdout.strip(),
                        f"CC={linker} GOARCH={arch} CGO_ENABLED=1",
                    )

    def test_router_images_share_one_dockerfile_and_publish_their_platforms(
        self,
    ) -> None:
        for image, (target, accelerator) in ROUTER_BUILDS.items():
            with self.subTest(image=image):
                _, dockerfile, platforms = DEFINITIONS[image]
                self.assertEqual(dockerfile, ROUTER_DOCKERFILE)
                self.assertEqual(target, "vllm-sr")
                expected = (
                    ["linux/amd64", "linux/arm64"]
                    if accelerator == "cpu"
                    else ["linux/amd64"]
                )
                self.assertEqual(platforms, expected)
        builder = (REPO_ROOT / ".github/workflows/build-artifacts.yml").read_text()
        publisher = (REPO_ROOT / ".github/workflows/docker-publish.yml").read_text()
        self.assertIn("image_artifacts.py", builder)
        self.assertIn("--multiarch", builder)
        self.assertIn("steps.definition.outputs.target", builder)
        self.assertIn("steps.definition.outputs.accelerator", builder)
        self.assertIn("image_artifacts.py promote", publisher)
        self.assertNotIn("docker/build-push-action", publisher)


if __name__ == "__main__":
    unittest.main()
