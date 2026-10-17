#!/usr/bin/env python3
"""Qualify a local RISC-V Router image via the maintained CLI and provider fixture.

No model inference is claimed: this checks keyword routing, forwarding, SSE,
CLI lifecycle and configuration persistence without weights or private services.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from http import HTTPStatus
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[3]
DIRECT = build_opener(ProxyHandler({}))
MAX_PORT_OFFSET = 40000


def check_response(raw, stream, expected):
    if stream:
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.endswith("\n\n"):
            raise ValueError("SSE event is missing its terminating blank line")
        events = []
        for frame in normalized.split("\n\n"):
            data = [
                line[5:].removeprefix(" ")
                for line in frame.splitlines()
                if line.startswith("data:")
            ]
            if data:
                events.append("\n".join(data))
        if not events or events[-1] != "[DONE]":
            raise ValueError("SSE did not terminate with [DONE]")
        chunks = [json.loads(item) for item in events[:-1]]
        if not chunks or any(item.get("model") != expected for item in chunks):
            raise ValueError("SSE selected the wrong model")
        text = "".join(
            choice.get("delta", {}).get("content") or ""
            for item in chunks
            for choice in item.get("choices", [])
        )
    else:
        item = json.loads(raw)
        if item.get("model") != expected or not item.get("choices"):
            raise ValueError(
                "buffered response selected the wrong model or has no choices"
            )
        text = item["choices"][0].get("message", {}).get("content") or ""
    if text != "ok":
        raise ValueError("provider fixture response content was not preserved")
    return text


def check_media_type(content_type, stream):
    expected = "text/event-stream" if stream else "application/json"
    if content_type.split(";", 1)[0].strip().lower() != expected:
        raise ValueError("response has the wrong media type")


def resume_command(command, original, active):
    if not active.is_file():
        raise FileNotFoundError("persisted runtime config is missing")
    original.rename(original.with_name("original-" + original.name))
    resumed = list(command)
    resumed[resumed.index("--config") + 1] = str(active)
    return resumed


def finish_cleanup(actions, receipt, report_path):
    errors = []
    try:
        for name, action in actions:
            try:
                action()
            except Exception as error:
                errors.append(f"{name}: {type(error).__name__}")
    finally:
        receipt["cleanup_errors"] = errors
        if errors:
            receipt["passed"] = False
        report_path.write_text(json.dumps(receipt, indent=2) + "\n")


def configuration(backend, listener):
    models = []
    for name in ("general-model", "code-model"):
        models.append(
            {
                "name": name,
                "provider_model_id": name,
                "api_format": "openai",
                "backend_refs": [
                    {
                        "name": "public-smoke",
                        "endpoint": backend,
                        "protocol": "http",
                        "weight": 100,
                    }
                ],
            }
        )
    return {
        "version": "v0.3",
        "listeners": [
            {
                "name": "public-smoke",
                "address": "0.0.0.0",
                "port": listener,
                "timeout": "30s",
            }
        ],
        "providers": {"defaults": {"model": "general-model"}, "models": models},
        "routing": {
            "modelCards": [
                {"name": name, "modality": "ar"}
                for name in ("general-model", "code-model")
            ],
            "signals": {
                "keywords": [
                    {
                        "name": "code",
                        "operator": "OR",
                        "keywords": ["write code"],
                        "case_sensitive": False,
                    }
                ]
            },
            "decisions": [
                {
                    "name": "code-route",
                    "priority": 100,
                    "rules": {
                        "operator": "AND",
                        "conditions": [{"type": "keyword", "name": "code"}],
                    },
                    "modelRefs": [{"model": "code-model"}],
                },
                {
                    "name": "general-route",
                    "priority": 1,
                    "rules": {"operator": "AND", "conditions": []},
                    "modelRefs": [{"model": "general-model"}],
                },
            ],
        },
        "entrypoints": [{"model_names": ["vllm-sr/auto"], "recipe": "default"}],
        "global": {
            "router": {"model_selection": {"enabled": False}},
            "services": {
                "response_api": {"enabled": False},
                "router_replay": {"enabled": False},
                "observability": {"tracing": {"enabled": False}},
            },
            "stores": {"response_cache": {"enabled": False}},
            "integrations": {"tools": {"enabled": False}},
            "model_catalog": {"kbs": []},
        },
    }


def wait_ready(url, process=None):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("provider fixture exited before readiness")
        try:
            with DIRECT.open(url, timeout=2) as response:
                if response.status == HTTPStatus.OK:
                    return
        except (OSError, URLError):
            pass
        time.sleep(1)
    raise RuntimeError(f"readiness timed out: {url}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--provider-python", required=True)
    parser.add_argument("--cli", default=shutil.which("vllm-sr"))
    parser.add_argument("--port-offset", type=int, default=12000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if platform.machine() != "riscv64" or platform.system() != "Linux":
        parser.error("run on native riscv64 Linux")
    if not args.cli:
        parser.error("install the CLI or supply --cli")
    if not 0 <= args.port_offset <= MAX_PORT_OFFSET:
        parser.error("port offset must be between 0 and 40000")
    output = (
        args.output or Path(tempfile.mkdtemp(prefix="vsr-native-smoke-"))
    ).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "result.json"
    if report_path.exists():
        parser.error("choose a fresh output directory")
    identity = "public-smoke-" + uuid.uuid4().hex[:10]
    backend_network = identity + "-backend"
    router_name = identity + "-vllm-sr-router-container"
    state = output / "state"
    config_path = output / "config.json"
    env = os.environ | {
        "VLLM_SR_STACK_NAME": identity,
        "VLLM_SR_PORT_OFFSET": str(args.port_offset),
        "VLLM_SR_STATE_ROOT_DIR": str(state),
    }
    receipt = {
        "machine": platform.machine(),
        "scope": "keyword routing with deterministic provider; no model inference",
        "cases": [],
        "passed": False,
    }
    provider = None
    created_network = False
    attempted_serve = False

    def run(command, name, check=True):
        with (output / (name + ".log")).open("w") as log:
            return subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=check,
                timeout=300,
            )

    try:
        image = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", args.image], text=True
            )
        )[0]
        if image["Architecture"] != "riscv64":
            raise RuntimeError("Router image is not riscv64")
        receipt["image"] = {"name": args.image, "id": image["Id"]}
        run(["docker", "network", "create", backend_network], "backend-network")
        created_network = True
        network = json.loads(
            subprocess.check_output(
                ["docker", "network", "inspect", backend_network], text=True
            )
        )[0]
        gateway = network["IPAM"]["Config"][0]["Gateway"]
        with socket.socket() as probe:
            probe.bind((gateway, 0))
            backend_port = probe.getsockname()[1]
        provider_env = os.environ | {
            "PROVIDER_MOCKER_SCENARIO": "cli",
            "PYTHONPATH": str(ROOT / "tools/test/services/provider-mocker"),
        }
        with (output / "provider.log").open("w") as log:
            provider = subprocess.Popen(
                [
                    args.provider_python,
                    "-m",
                    "provider_mocker",
                    "--host",
                    gateway,
                    "--port",
                    str(backend_port),
                ],
                env=provider_env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        wait_ready(f"http://{gateway}:{backend_port}/health", provider)
        listener = 19000
        config_path.write_text(
            json.dumps(configuration(f"{gateway}:{backend_port}", listener), indent=2)
            + "\n"
        )
        serve = [
            args.cli,
            "serve",
            "--config",
            str(config_path),
            "--platform",
            "cpu",
            "--gateway",
            "standalone",
            "--minimal",
            "--image-pull-policy",
            "never",
            "--router-image",
            args.image,
            "--startup-timeout",
            "120",
        ]
        active = state / ".vllm-sr" / f"runtime-config.{identity}.yaml"
        previous_id = None
        config_hash = None
        for phase in ("initial", "restart"):
            if phase == "restart":
                serve = resume_command(serve, config_path, active)
            attempted_serve = True
            run(serve, phase + "-serve")
            run([args.cli, "status"], phase + "-status")
            url = f"http://127.0.0.1:{listener + args.port_offset}"
            wait_ready(url + "/ready")
            container = json.loads(
                subprocess.check_output(["docker", "inspect", router_name], text=True)
            )[0]
            if container["Image"] != image["Id"] or container["Id"] == previous_id:
                raise RuntimeError(
                    "CLI did not start a fresh container with the expected image"
                )
            previous_id = container["Id"]
            digest = hashlib.sha256(active.read_bytes()).hexdigest()
            if config_hash is not None and digest != config_hash:
                raise RuntimeError("runtime configuration changed across restart")
            config_hash = digest
            for prompt, expected in (
                ("write code for binary search", "code-model"),
                ("Explain photosynthesis", "general-model"),
            ):
                for stream in (False, True):
                    body = {
                        "model": "vllm-sr/auto",
                        "stream": stream,
                        "messages": [{"role": "user", "content": prompt}],
                    }
                    request = Request(
                        url + "/v1/chat/completions",
                        data=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with DIRECT.open(request, timeout=30) as response:
                        check_media_type(
                            response.headers.get("Content-Type", ""), stream
                        )
                        raw = response.read().decode()
                        check_response(raw, stream, expected)
                        receipt["cases"].append(
                            {
                                "phase": phase,
                                "stream": stream,
                                "model": expected,
                                "status": response.status,
                            }
                        )
            run([args.cli, "logs"], phase + "-logs", check=False)
            run([args.cli, "stop"], phase + "-stop")
            attempted_serve = False
        receipt["runtime_config_sha256"] = config_hash
        receipt["passed"] = True
    finally:
        actions = []
        if attempted_serve:
            actions.append(("stop", lambda: run([args.cli, "stop"], "cleanup-stop")))
        if provider is not None:

            def stop_provider():
                provider.terminate()
                try:
                    provider.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    provider.kill()
                    provider.wait(timeout=10)

            actions.append(("provider", stop_provider))
        if created_network:
            actions.append(
                (
                    "network",
                    lambda: run(
                        ["docker", "network", "rm", backend_network],
                        "cleanup-backend-network",
                    ),
                )
            )
        finish_cleanup(actions, receipt, report_path)
    if not receipt["passed"]:
        raise RuntimeError(f"acceptance failed; see {report_path}")
    print(json.dumps({"passed": len(receipt["cases"]), "report": str(report_path)}))


if __name__ == "__main__":
    main()
