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
    def test_router_build_uses_only_the_required_target_c_linker(self) -> None:
        command = router_build_command()
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp)
            (stubs / "dpkg").write_text('#!/bin/sh\necho "$BUILD_HOST_ARCH"\n')
            (stubs / "go").write_text(
                '#!/bin/sh\necho "CC=${CC:-} GOARCH=$GOARCH CGO_ENABLED=$CGO_ENABLED"\n'
                'if [ "$GOARCH" = riscv64 ]; then printf "%s\\n" "$@"; fi\n'
            )
            for stub in stubs.iterdir():
                stub.chmod(0o755)
            for arch, host, linker in (
                ("arm64", "amd64", "aarch64-linux-gnu-gcc"),
                ("amd64", "amd64", ""),
                ("riscv64", "amd64", "riscv64-linux-gnu-gcc"),
                ("riscv64", "arm64", "riscv64-linux-gnu-gcc"),
                ("riscv64", "riscv64", ""),
                ("arm64", "arm64", ""),
            ):
                with self.subTest(arch=arch, host=host):
                    environment = {
                        key: value for key, value in os.environ.items() if key != "CC"
                    } | {
                        "PATH": f"{stubs}:{os.environ['PATH']}",
                        "TARGETARCH": arch,
                        "BUILD_HOST_ARCH": host,
                    }
                    result = subprocess.run(
                        ["bash", "-e", "-c", command],
                        env=environment,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        result.stdout.splitlines()[0],
                        f"CC={linker} GOARCH={arch} CGO_ENABLED=1",
                    )
                    if arch == "riscv64":
                        self.assertIn("-extldflags=-L/out/glide", result.stdout)

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
