# Docker build files

This directory contains the Router image and lint toolchain Dockerfiles:

| File | Purpose |
| --- | --- |
| `Dockerfile.extproc` | Every Router image: the Go router with the built-in model runtime. |
| `Dockerfile.router-attached` | Router without bundled model inference; amd64, arm64 and riscv64 build targets. |
| `Dockerfile.riscv64-toolchain` | Native riscv64 Go/Rust builder from checksum-verified official archives. |
| `Dockerfile.precommit` | Reproducible lint and agent-harness toolchain used by CI. |

`Dockerfile.extproc` builds the three published Router images from one stage
graph and one target, `vllm-sr`. The router binary and the image-routing
assets are shared; `ACCELERATOR` selects the runtime's PyTorch build. Each
image serves `vllm-sr serve`, the Helm chart and the Operator, in standalone
or extproc mode:

| Image | `ACCELERATOR` | Platforms |
| --- | --- | --- |
| `vllm-sr` | `cpu` (default) | amd64, arm64 |
| `vllm-sr-rocm` | `rocm` | amd64 |
| `vllm-sr-cuda` | `cuda` | amd64 |

For one release, `vllm-sr` is also published as `extproc` and `vllm-sr-rocm`
as `extproc-rocm`, with the same digests, for deployments that still name the
former Kubernetes images.

The entrypoint, `/app/start-router.sh`, runs the Router on
`/app/config/config.yaml` when it gets no arguments or Router flags (the Helm
chart and the Operator pass `-gateway=...`), and starts the Router of a
`vllm-sr serve` stack when it gets a config path, as the CLI passes.

Build from the repository root so the Dockerfile can access every module:

```bash
docker build -f tools/docker/Dockerfile.extproc -t vllm-sr:local .
docker build -f tools/docker/Dockerfile.extproc --build-arg ACCELERATOR=rocm \
  -t vllm-sr-rocm:local .
```

CPU images carry CPU-only PyTorch. GPU images take PyTorch from the official
ROCm or CUDA wheels, which include their user-space GPU libraries, so they
need only the host's GPU driver and device access at runtime. The router
process has no native model code: every model runs in the runtime it starts.

## CI and publication

Pull-request builds go through
[`build-artifacts.yml`](../../.github/workflows/build-artifacts.yml), and
main, nightly and release publication through
[`docker-publish.yml`](../../.github/workflows/docker-publish.yml). The image
definitions (Dockerfile, target, platforms) live in
[`tools/ci/image_artifacts.py`](../ci/image_artifacts.py); this README does not
duplicate that inventory.

When editing the Dockerfile, keep dependency-only layers ahead of source
copies: PyTorch and the runtime's dependencies change rarely, the router binary
and configuration often.

## Attached model runtime

`router-attached` is a separate candidate image for deployments whose model
runtime is managed elsewhere. It does not replace `vllm-sr` and does not install
PyTorch or `vllm-srun`. Configure every active model-runtime deployment with an
attached `endpoint` and matching `served_name`; mount your canonical Router
configuration at `/app/config/config.yaml`. The repository's bundled-runtime
example is not an attached deployment configuration.

```bash
docker buildx build --platform linux/amd64,linux/arm64,linux/riscv64 \
  -f tools/docker/Dockerfile.router-attached --target router-attached \
  -t registry.example.com/router-attached:dev .
```

Go compilation and the real riscv64 GLIDE FFI build run on the build host.
The target Debian image's package installation requires binfmt/QEMU for a
cross build (or native target workers). The riscv64 FFI uses the source hash
and Cargo lock described in [valkey-glide/README.md](valkey-glide/README.md).
The image keeps the standard Router entrypoint, CLI configuration assets,
knowledge-base files and frozen image-routing prototypes. Python is present
for the CLI entrypoint, not for model inference. A BusyBox `mkfifo` avoids the
mode-setting failure observed with GNU mkfifo on the qualified container host;
seccomp is not disabled.

Pin `GO_IMAGE`, `RUST_IMAGE` and `BASE_IMAGE` to immutable references for release
candidates and record their digests. `tools/ci/image_artifacts.py` understands
this candidate and `operator-portable`, whose source Dockerfile is
`deploy/operator/Dockerfile.portable`. The latter is a non-root static Go
controller in a scratch image, without replacing the existing UBI image.
Build definitions and candidate receipts do not by themselves certify any
platform: run and qualify each target digest before a supported release.

### Native RISC-V builds without emulation

The default Go/Rust builder images are intended for the cross-build host.
For a native riscv64 worker, prepare a dedicated archive directory containing
`go1.25.14.linux-riscv64.tar.gz` from `https://go.dev/dl/` and the three
`rustc`, `cargo`, and `rust-std` 1.90.0 archives for
`riscv64gc-unknown-linux-gnu` from
`https://static.rust-lang.org/dist/2025-09-18/`. The native builder verifies
all four SHA-256 checksums before installing anything from the archives.

Run on the RISC-V worker, from the repository root:

```bash
docker build -f tools/docker/Dockerfile.riscv64-toolchain \
  -t vsr-native-toolchain:go1.25.14-rust1.90 /path/to/archive-directory
docker buildx build --platform linux/riscv64 \
  --build-arg TARGETARCH=riscv64 --build-arg BUILDPLATFORM=linux/riscv64 \
  --build-arg GO_IMAGE=vsr-native-toolchain:go1.25.14-rust1.90 \
  --build-arg RUST_IMAGE=vsr-native-toolchain:go1.25.14-rust1.90 \
  -f tools/docker/Dockerfile.router-attached --target router-attached \
  -t router-attached:riscv64-native --load .
```

No QEMU/binfmt registration is needed. Explicit architecture arguments also
work with older native BuildKit frontends. Native compilation uses `gcc` and
the installed Rust target standard library; cross builds retain the target
compiler installation. The native builder limits Rust and Go parallelism to
two workers for the qualified 16 GiB server.

The builder uses BusyBox `tar`: on the qualified container environment,
GNU tar fails when restoring directory modes, while the same directory-only
archive extracts successfully with BusyBox. This workaround is container-local
and does not disable seccomp or replace the host's tools. Record the builder
Dockerfile, archive hashes, base image digest and resulting image identity with
the build receipts; use immutable builder/base references for release builds.

If the build worker cannot reach the default Go module proxy, pass a reachable
proxy with `--build-arg 'GOPROXY=https://goproxy.cn|direct'`. This changes the
download route, not `go.mod`, `go.sum`, or checksum verification. Keep the
module checksum database enabled.

### Dashboard native builder overrides

`dashboard/backend/Dockerfile` keeps its existing Go/Alpine, Node/Alpine and
Python/Bookworm defaults. Native workers whose architecture is missing from
those image indexes can override `WASM_GO_IMAGE`, `FRONTEND_IMAGE`, `GO_IMAGE`
and `RUNTIME_IMAGE`. The Go builder must contain Go 1.25 and a native C compiler:
the Dashboard still builds with CGO and its real SQLite implementation.

The locked Rolldown 1.2.7 package has no RISC-V native binding. Build the
frontend/WASM on a supported native worker and transfer only the static output;
the Go backend and runtime image still build on RISC-V. From a clean directory
on the supported worker, with the same source revision and frontend lockfile:

```bash
docker buildx build -f dashboard/backend/Dockerfile --target frontend-builder \
  --output type=local,dest=frontend-stage .
tar -czf dashboard-frontend-context.tar.gz -C frontend-stage \
  workspace/dashboard/frontend/dist
```

Extract that archive into a dedicated context directory on the RISC-V worker.
Keep the `workspace/dashboard/frontend/dist` prefix. Record its checksum and the
source image/build identity; do not transfer Node, npm, node_modules or ELF
executables. BuildKit's [named contexts](https://docs.docker.com/reference/cli/docker/buildx/build/#build-context)
can override the frontend stage without changing the default build graph.

After preparing the native toolchain above, run on the RISC-V worker from the
repository root:

```bash
docker buildx build --platform linux/riscv64 \
  --build-context frontend-builder=/path/to/frontend-context \
  --build-arg TARGETARCH=riscv64 --build-arg TARGETOS=linux \
  --build-arg BUILDPLATFORM=linux/riscv64 \
  --build-arg WASM_GO_IMAGE=vsr-native-toolchain:go1.25.14-rust1.90 \
  --build-arg GO_IMAGE=vsr-native-toolchain:go1.25.14-rust1.90 \
  --build-arg FRONTEND_IMAGE=debian:trixie-slim \
  --build-arg RUNTIME_IMAGE=debian:trixie-slim \
  -f dashboard/backend/Dockerfile -t dashboard:riscv64-native --load .
```

Without the static-context override, the plain Debian frontend installs
distribution Node/npm and uses the existing `npm ci` lockfile, but the current
Rolldown binding still prevents a fully native RISC-V frontend build. The runtime installs
Python/venv when its base does not provide them, then installs the existing CLI
requirements. These overrides do not remove the WASM compiler, Python catalog
exporter, authentication database, bundled assets or permission initialization.
`GOPROXY` is also an optional build argument, with the same default as the Router.

RISC-V Dashboard builds require the base-image overrides and static frontend
context above. The default Dashboard CI platform inventory remains amd64/arm64.
Pin base and builder digests and record the resolved Node, Python and CLI
dependencies for reproducible builds.

### CLI configuration history permissions

The split Docker launcher explicitly enables shared configuration history for
the Router and Dashboard. It uses the existing CLI/Dashboard share group (never
GID 0), and keeps histories separated by stack. Direct deployments that do not set
`VLLM_SR_CONFIG_HISTORY_GID` retain owner-only directory/file modes 0700/0600.

In the CLI stack, directories use 2770 and history records/locks use 0660.
Before creating containers, the host CLI prepares
`<state-root>/.vllm-sr-history/<stack>` under a CLI-owned 0700 parent and mounts
only that stack's directory at `/app/config-history`. The parent is not exposed
to the containers. `VLLM_SR_CONFIG_BACKUP_DIR` selects this mount; neither
entrypoint recursively changes permissions. The Dashboard still drops to its
nonroot user. Directory symlinks, unexpected owners and group mismatches fail
closed. Management credentials and authentication/session data remain outside
this mount. Membership of the selected host group must be restricted to trusted
configuration writers.

Existing `.vllm-sr/config-backups` directories are left unchanged. They are not
automatically imported: this avoids privileged traversal of a directory below
Dashboard-writable state, and does not disturb another running stack. Old restore
points remain on disk; use the original deployment to export a required version
before switching history locations. The new history begins with the current
configuration; this change does not delete old backups.

This shared-history path is qualified with rootful Docker. Podman retains its
previous private-history behavior; its user-namespace GID mapping is not covered
by this change. The launcher checks the daemon's reported platform and security
options, not just the executable name; Podman-compatible daemons, rootless or
userns-remapped Docker, failed probes and unknown responses skip both history
preparation and the new mount. Both state directories are excluded from Recipe archives, with
symlinked state directories still rejected.
