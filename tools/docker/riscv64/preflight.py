#!/usr/bin/env python3
"""Read-only checks for the minimal native standalone deployment."""

import argparse
import ipaddress
import platform
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def validate_backend(url, model):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("Backend URL is malformed") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in url)
        or port == 0
    ):
        raise ValueError(
            "Use an HTTP(S) backend URL without credentials, query or fragment"
        )
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if (
        host == "localhost"
        or host.endswith(".localhost")
        or (address is not None and (address.is_loopback or address.is_unspecified))
    ):
        raise ValueError(
            "Backend must be reachable from the Router container, not localhost"
        )
    if not model.strip():
        raise ValueError("Set provider_model_id to the backend's served model ID")


def docker_output(*args):
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(
            "Docker is unavailable; check installation and connection"
        ) from None
    if result.returncode:
        raise ValueError(
            "Docker check failed; check daemon access and local image availability"
        )
    return result.stdout.strip()


def check_docker(image):
    daemon = docker_output("info", "--format", "{{.OSType}}/{{.Architecture}}")
    if daemon != "linux/riscv64":
        raise ValueError("Docker daemon must run native linux/riscv64")
    image_platform = docker_output(
        "image", "inspect", image, "--format", "{{.Os}}/{{.Architecture}}"
    )
    if image_platform != "linux/riscv64":
        raise ValueError("The selected local image must be linux/riscv64")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="semantic-router-router:public")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            config = yaml.safe_load(args.config.read_text())
            models = config["providers"]["models"]
            for model in models:
                for backend in model["backend_refs"]:
                    validate_backend(
                        backend.get("base_url", ""), model.get("provider_model_id", "")
                    )
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError):
            raise ValueError(
                "Cannot read minimal deployment config; run CLI config validate"
            ) from None
        if platform.system() != "Linux" or platform.machine() != "riscv64":
            raise ValueError("Run this deployment on native riscv64 Linux")
        check_docker(args.image)
    except ValueError as error:
        parser.exit(1, f"Preflight failed: {error}\n")
    print("Preflight passed: backend inputs and native Docker image checked.")
    print(
        "Backend connectivity, authentication and generation still require a live request."
    )


if __name__ == "__main__":
    main()
