"""The launcher shares only configuration history, never sibling credentials."""

import json
import os
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest
from cli import container_start, container_start_paths, history_permissions
from cli.container_start_paths import _runtime_mount_specs
from cli.history_permissions import prepare_history, supports_shared_history
from cli.runtime_stack import resolve_runtime_stack


def test_history_preparation_leaves_existing_files_and_credentials_private(tmp_path):
    history_root = tmp_path / ".vllm-sr-history"
    history_root.mkdir(mode=0o700)
    history = history_root / "stack-a"
    history.mkdir(mode=0o2770)
    record = history / "config.20261006-123000.yaml"
    record.write_text("synthetic-provider-credential")
    record.chmod(0o600)
    credential = tmp_path / "management.token"
    credential.write_text("synthetic-management-credential")
    credential.chmod(0o600)
    gid = os.getgid() or 65532
    result = subprocess.run(
        [sys.executable, "-m", "cli.history_permissions", str(history), str(gid)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(history.stat().st_mode) == 0o2770
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert stat.S_IMODE(credential.stat().st_mode) == 0o600
    assert credential.read_text() == "synthetic-management-credential"


def test_history_preparation_rejects_directory_symlink(tmp_path):
    root = tmp_path / ".vllm-sr-history"
    root.mkdir(mode=0o700)
    history = root / "history"
    history.mkdir()
    target = tmp_path / "private-token"
    target.write_text("synthetic-private-token")
    target.chmod(0o600)
    history.rmdir()
    history.symlink_to(tmp_path, target_is_directory=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.history_permissions",
            str(history),
            str(os.getgid() or 65532),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == "synthetic-private-token"


def test_history_preparation_refuses_root_group_before_creating_state(tmp_path):
    history = tmp_path / "history"
    result = subprocess.run(
        [sys.executable, "-m", "cli.history_permissions", str(history), "0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not history.exists()


def test_standard_stack_selects_only_its_existing_private_share_group():
    env = container_start._build_common_runtime_env(
        {"VLLM_SR_CONFIG_HISTORY_GID": "0", "VLLM_SR_CONFIG_BACKUP_DIR": "/untrusted"},
        resolve_runtime_stack(stack_name="history-test"),
        runtime_container_config="/app/.vllm-sr/runtime-config.history-test.yaml",
    )
    assert env["VLLM_SR_CONFIG_HISTORY_GID"] == str(os.getgid() or 65532)
    assert "VLLM_SR_CONFIG_HISTORY_ROOT" not in env
    assert env["VLLM_SR_CONFIG_BACKUP_DIR"] == "/app/config-history"


def test_history_rejects_dashboard_writable_parent_before_changing_credentials(
    tmp_path,
):
    parent = tmp_path / ".vllm-sr-history"
    parent.mkdir(mode=0o770)
    substituted = parent / "stack-a"
    substituted.mkdir(mode=0o700)
    credential = substituted / "management.token"
    credential.write_text("synthetic-private-token")
    credential.chmod(0o600)
    with pytest.raises(ValueError, match="private"):
        prepare_history(str(substituted), os.getgid() or 65532)
    assert stat.S_IMODE(substituted.stat().st_mode) == 0o700
    assert stat.S_IMODE(credential.stat().st_mode) == 0o600


def test_history_startup_does_not_walk_records_or_other_stacks(tmp_path, monkeypatch):
    root = tmp_path / ".vllm-sr-history"
    history = root / "stack-a"
    gid = os.getgid() or 65532
    prepare_history(str(history), gid)
    # A crash between link publication and unlink leaves two valid names.
    record = history / "config.version.yaml"
    record.write_text("synthetic-config")
    record.chmod(0o660)
    os.link(record, history / ".config.version.yaml.tmp")
    other = root / "stack-b"
    other.mkdir(mode=0o700)

    def no_walk(*args, **kwargs):
        raise AssertionError("startup must not traverse live history")

    monkeypatch.setattr(os, "fwalk", no_walk)
    prepare_history(str(history), gid)
    assert record.stat().st_nlink == 2
    assert stat.S_IMODE(other.stat().st_mode) == 0o700


def test_history_mount_is_outside_dashboard_writable_state(tmp_path):
    state = tmp_path / ".vllm-sr"
    state.mkdir()
    history = tmp_path / ".vllm-sr-history" / "stack-a"
    prepare_history(str(history), os.getgid() or 65532)
    paths = {
        "source_config_path": str(tmp_path / "config.yaml"),
        "vllm_sr_dir": str(state),
        "log_spool_logs_root": str(state / "logs"),
        "history_dir": str(history),
    }
    assert f"{history}:/app/config-history:z" in _runtime_mount_specs(paths)
    assert not history.is_relative_to(state)


def test_unqualified_runtime_keeps_existing_private_history_policy():
    env = container_start._build_common_runtime_env(
        {
            "VLLM_SR_CONFIG_HISTORY_GID": "123",
            "VLLM_SR_CONFIG_BACKUP_DIR": "/untrusted",
        },
        resolve_runtime_stack(stack_name="podman-test"),
        runtime_container_config="/app/.vllm-sr/runtime-config.podman-test.yaml",
        shared_history=False,
    )
    assert "VLLM_SR_CONFIG_HISTORY_GID" not in env
    assert "VLLM_SR_CONFIG_BACKUP_DIR" not in env


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_disabled_history_does_not_prepare_paths(tmp_path, monkeypatch, runtime):
    config = tmp_path / "config.yaml"
    config.write_text("version: v0.3\nlisteners: []\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled history preparation was called")

    monkeypatch.setattr(container_start_paths, "prepare_history", forbidden)
    _, paths, _ = container_start_paths._prepare_runtime_paths(
        str(config), shared_history=False
    )
    assert not paths.get("history_dir")
    assert not (tmp_path / ".vllm-sr-history").exists()


@pytest.mark.parametrize(
    ("platform", "security", "expected"),
    [
        ("Docker Engine - Community", ["name=seccomp"], True),
        ("Podman Engine", [], False),
        ("Docker Engine - Community", ["name=rootless"], False),
        ("Docker Engine - Community", ["name=userns"], False),
    ],
)
def test_shared_history_uses_daemon_identity(monkeypatch, platform, security, expected):
    def run(command, **kwargs):
        payload = (
            {"Server": {"Platform": {"Name": platform}}}
            if command[1] == "version"
            else {"SecurityOptions": security}
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(history_permissions.subprocess, "run", run)
    assert supports_shared_history("docker") is expected
    assert not supports_shared_history("podman")


def test_distribution_docker_with_empty_platform_is_identified_by_engine_components(
    monkeypatch,
):
    def run(command, **kwargs):
        payload = (
            {
                "Server": {
                    "Platform": {"Name": ""},
                    "Components": [
                        {"Name": "Engine"},
                        {"Name": "containerd"},
                        {"Name": "runc"},
                        {"Name": "docker-init"},
                    ],
                }
            }
            if command[1] == "version"
            else {"SecurityOptions": ["name=seccomp", "name=cgroupns"]}
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(history_permissions.subprocess, "run", run)
    assert supports_shared_history("docker")
