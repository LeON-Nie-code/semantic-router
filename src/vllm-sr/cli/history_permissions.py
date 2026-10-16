"""Host-side preparation of an isolated per-stack history bind mount.

Existing records are never walked, imported or chmodded. Only directories
owned by the invoking CLI user are prepared, outside writable container state.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from contextlib import suppress

from cli.container_runtime import _docker_daemon_looks_like_podman

DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
MIN_HISTORY_COMPONENTS = 3
PRIVATE_PARENT_MODE = 0o700
SHARED_DIRECTORY_MODE = 0o2770


def supports_shared_history(runtime: str) -> bool:
    """Opt in only for a positively identified, non-remapped Docker daemon."""
    if sys.platform != "linux" or runtime != "docker":
        return False
    try:
        payloads = []
        for operation in ("version", "info"):
            result = subprocess.run(
                [runtime, operation, "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode != 0:
                return False
            payloads.append(json.loads(result.stdout))
        version, info = payloads
        if _docker_daemon_looks_like_podman(version):
            return False
        platform = version.get("Server", {}).get("Platform", {}).get("Name", "")
        components = {
            component.get("Name", "").lower()
            for component in version.get("Server", {}).get("Components", [])
        }
        security = info.get("SecurityOptions")
        if not isinstance(platform, str) or not isinstance(security, list):
            return False
        return (
            (
                "docker" in platform.lower()
                or {"engine", "containerd", "runc", "docker-init"}.issubset(components)
            )
            and "podman" not in platform.lower()
            and all(
                isinstance(option, str)
                and not any(
                    marker in option.lower() for marker in ("rootless", "userns")
                )
                for option in security
            )
        )
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError, TypeError):
        return False


def _open_parent(path: str) -> tuple[int, str]:
    if not os.path.isabs(path):
        raise ValueError("history directory must be absolute")
    parts = [part for part in path.split(os.sep) if part]
    if len(parts) < MIN_HISTORY_COMPONENTS or any(
        part in (".", "..") for part in parts
    ):
        raise ValueError("history directory must be a dedicated child directory")
    descriptor = os.open(os.sep, DIRECTORY_FLAGS)
    try:
        for part in parts[:-2]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        with suppress(FileExistsError):
            os.mkdir(parts[-2], PRIVATE_PARENT_MODE, dir_fd=descriptor)
        parent = os.open(parts[-2], DIRECTORY_FLAGS, dir_fd=descriptor)
        info = os.fstat(parent)
        if (
            info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != PRIVATE_PARENT_MODE
        ):
            os.close(parent)
            raise ValueError("history parent must be private to the current CLI user")
        return parent, parts[-1]
    finally:
        os.close(descriptor)


def prepare_history(path: str, gid: int) -> None:
    if sys.platform != "linux":
        raise ValueError("shared history preparation requires a Linux launcher")
    if not 0 < gid < 2**32:
        raise ValueError("history group must be a non-root private group")
    parent, name = _open_parent(path)
    try:
        created = False
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        directory = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid():
                raise ValueError("history must be owned by the current CLI user")
            if created and info.st_gid != gid:
                os.fchown(directory, -1, gid)
            elif info.st_gid != gid:
                raise ValueError("history must belong to the CLI private group")
            if stat.S_IMODE(info.st_mode) != SHARED_DIRECTORY_MODE:
                os.fchmod(directory, SHARED_DIRECTORY_MODE)
        finally:
            os.close(directory)
    finally:
        os.close(parent)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("gid", type=int)
    args = parser.parse_args()
    prepare_history(args.directory, args.gid)


if __name__ == "__main__":
    main()
