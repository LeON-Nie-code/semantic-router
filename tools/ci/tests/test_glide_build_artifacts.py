"""Exercise the GLIDE build boundary without compiling its Rust dependency tree."""

import hashlib
import io
import os
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE_SHA = "861a5345b423206fa9090925767b3f9524e4bf135b851beaa6ccf41eb4153e53"
TARGET = "riscv64gc-unknown-linux-gnu"


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("ar", "readelf", "sha256sum", "tar")),
    "GNU archive inspection tools required",
)
class GlideBuildArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        scripts = self.repo / "tools/docker"
        (scripts / "valkey-glide").mkdir(parents=True)
        module = self.repo / "src/semantic-router/go.mod"
        module.parent.mkdir(parents=True)
        module.write_text(
            "require (\n github.com/valkey-io/valkey-glide/go/v2 v2.3.0\n)\n"
        )
        shutil.copyfile(
            ROOT / "tools/docker/valkey-glide/Cargo.lock",
            scripts / "valkey-glide/Cargo.lock",
        )
        self.archive = self.root / "source.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            content = b'[package]\nname="glide-fixture"\nversion="0.1.0"\n'
            member = tarfile.TarInfo("valkey-glide/ffi/Cargo.toml")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        # Only this isolated copy pins a tiny source fixture. SHA verification,
        # extraction, lock installation and GNU ELF inspection remain real.
        source = (ROOT / "tools/docker/build-glide.sh").read_text()
        self.script = scripts / "build-glide.sh"
        self.script.write_text(
            source.replace(
                SOURCE_SHA, hashlib.sha256(self.archive.read_bytes()).hexdigest()
            )
        )
        self.tools = self.root / "tools"
        self.tools.mkdir()
        cargo = self.tools / "cargo"
        cargo.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, shutil, sys\n"
            f"assert sys.argv[1:] == ['build', '--locked', '--release', '--target', '{TARGET}']\n"
            "assert pathlib.Path('Cargo.toml').is_file()\n"
            "assert pathlib.Path('Cargo.lock').read_bytes() == pathlib.Path(os.environ['TEST_LOCK']).read_bytes()\n"
            "pathlib.Path(os.environ['TEST_CALLED']).touch()\n"
            "if os.environ.get('TEST_CARGO_FAIL'): sys.exit(42)\n"
            f"output = pathlib.Path(os.environ['CARGO_TARGET_DIR']) / '{TARGET}/release/libglide_ffi.a'\n"
            "output.parent.mkdir(parents=True, exist_ok=True)\n"
            "shutil.copyfile(os.environ['TEST_LIBRARY'], output)\n"
        )
        cargo.chmod(0o755)
        self.library = self.root / "fixture.a"
        self.output = self.root / "installed"
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.called = self.root / "cargo-called"

    def make_library(self, *machines):
        objects = []
        for index, machine in enumerate(machines):
            path = self.root / f"object-{index}.o"
            # Minimal ELF64 relocatable objects: no instructions or extensions.
            ident = b"\x7fELF\x02\x01\x01" + bytes(9)
            path.write_bytes(
                struct.pack(
                    "<16sHHIQQQIHHHHHH",
                    ident,
                    1,
                    machine,
                    1,
                    0,
                    0,
                    0,
                    0,
                    64,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            )
            objects.append(str(path))
        subprocess.run(["ar", "crs", str(self.library), *objects], check=True)

    def build(self, **extra):
        return subprocess.run(
            [
                "bash",
                str(self.script),
                "--archive",
                str(self.archive),
                "--output",
                str(self.output),
            ],
            env=os.environ
            | {
                "PATH": str(self.tools) + os.pathsep + os.environ["PATH"],
                "CARGO_TARGET_DIR": str(self.root / "cargo cache"),
                "TMPDIR": str(self.scratch),
                "TEST_LIBRARY": str(self.library),
                "TEST_LOCK": str(ROOT / "tools/docker/valkey-glide/Cargo.lock"),
                "TEST_CALLED": str(self.called),
            }
            | extra,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_verified_riscv_archive_is_installed_with_mode_0644(self):
        self.make_library(243, 243)
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.output / "libglide_ffi.a"
        self.assertEqual(installed.read_bytes(), self.library.read_bytes())
        self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o644)
        self.assertTrue(self.called.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_wrong_or_mixed_architecture_never_replaces_existing_library(self):
        self.output.mkdir()
        installed = self.output / "libglide_ffi.a"
        for machines in ((62,), (243, 62)):
            with self.subTest(machines=machines):
                if self.library.exists():
                    self.library.unlink()
                self.make_library(*machines)
                installed.write_bytes(b"previous verified library")
                result = self.build()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unexpected ELF machines", result.stderr)
                self.assertEqual(installed.read_bytes(), b"previous verified library")
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_empty_archive_is_rejected_without_installing(self):
        self.make_library()
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_corrupt_source_never_reaches_cargo(self):
        self.make_library(243)
        self.archive.write_bytes(b"corrupt source")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source checksum mismatch", result.stderr)
        self.assertFalse(self.called.exists())
        self.assertFalse(self.output.exists())

    def test_cargo_failure_propagates_and_cleans_scratch(self):
        self.make_library(243)
        result = self.build(TEST_CARGO_FAIL="1")
        self.assertEqual(result.returncode, 42)
        self.assertTrue(self.called.exists())
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
