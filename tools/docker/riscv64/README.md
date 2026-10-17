# Native RISC-V builds from public inputs

This flow builds a Linux RISC-V SDK and the standalone Router without copying
host toolchains, module caches, compiler caches, model files, or private build
contexts. It does not change the published image platform inventory.

## Requirements

- Native `riscv64` Linux, Docker Engine with BuildKit/buildx's `docker` driver,
  Python 3.10+. The runner checks the driver before building; an Engine-local
  SDK cannot be handed implicitly to an isolated `docker-container` builder.
- A glibc-compatible container host and the standard RISC-V GNU user ISA required
  by the upstream Go/Rust release binaries. No vendor CPU extensions are enabled
  by this recipe. This does not claim every RISC-V board has been validated.
- Internet access to Docker Hub, Debian repositories, Go and Rust downloads,
  crates.io, GitHub and the Go module proxy. Runtime qualification also needs
  PyPI. An ordinary operator-managed HTTPS proxy can be used if required.
- Allow substantial disk space and native compilation time. On an 8-core,
  16-GB machine the SDK restricts Go and Cargo build concurrency to two.

The base Debian **RISC-V manifest** is pinned by digest. Go 1.25.14 and Rust
1.90.0 archives are pinned by URL and SHA-256 in `toolchains.json`, sourced from
[Go downloads](https://go.dev/dl/) and
[Rust releases](https://static.rust-lang.org/dist/2025-09-18/channel-rust-1.90.0.toml).
GLIDE has a separate source hash and Cargo lock in the parent directory.
Debian package repositories remain live, so this is a reproducible build
procedure, not a promise of bit-for-bit identical image digests over time.

The SDK uses Debian's BusyBox tar. GNU tar in trixie uses a directory-mode
operation denied by some older container seccomp profiles; changing the archive
utility keeps sandboxing enabled. An extraction content/permission probe runs
before installing the toolchains. The final SDK also checks tool versions and
the absence of preloaded Go build and Cargo registry caches.
The upstream [Docker seccomp update](https://github.com/moby/moby/pull/47341)
documents the addition of `fchmodat2` to the default profile.

## Build

From a clean checkout of the source revision being tested, at repository root:

```bash
python3 tools/docker/riscv64/build.py --plan
python3 tools/docker/riscv64/build.py --component router --tag public \
  --output /tmp/vsr-router-build
```

The second command first builds `semantic-router-native-sdk:public`, then
`semantic-router-router:public`. Both use `--no-cache`; neither imports cache
mounts or an external build cache. The Router selects the explicit
`vllm-sr-attached` target and links the source-built GLIDE FFI. Model-runtime
endpoints must be configured separately when model-based signals are enabled.

`--component sdk` builds only the SDK. `--component operator` uses the same SDK
recipe to build the Operator. These are local image builds, not publication.
The runner records commands, step exit codes and the final image ID in
`build.json`, with separate build logs. Use a new output directory for each run.

Changing a mirror is explicit, for example `--base-image REGISTRY/library/debian@sha256:DIGEST`.
The digest must identify the same intended RISC-V base; a mutable tag is rejected.
`--go-proxy URL` selects an optional Go module mirror. No mirror or proxy address
specific to a developer machine is required by the source tree.

If the host needs a proxy, configure it outside this repository. Docker's
predefined HTTP/HTTPS proxy build arguments are forwarded from the invocation
environment; their values are not written into the Dockerfile. `--network host`
is available on native Linux when an operator's proxy requires host networking.
The Docker daemon's registry access must also be configured by the operator.
Do not commit proxy credentials or machine-specific environment files.

`--no-cache` rebuilds instructions but does not remove existing host images.
For an empty-cache qualification, use a separate disposable builder/daemon or
an otherwise clean environment; do not prune a shared machine's Docker state.

## Automated standalone qualification

Use fresh environments rather than an existing CLI or provider installation:

```bash
VSR_QUALIFY_WORK=$(mktemp -d)
# PyPI does not provide wheels for every Python/RISC-V combination. Reuse only
# the release toolchain from the newly built SDK, not any host Cargo cache.
VSR_SDK_EXPORT=$(docker create semantic-router-native-sdk:public /bin/true)
docker cp "$VSR_SDK_EXPORT:/opt/rust" "$VSR_QUALIFY_WORK/"
docker rm "$VSR_SDK_EXPORT"
export PATH="$VSR_QUALIFY_WORK/rust/bin:$PATH"
export CARGO_HOME="$VSR_QUALIFY_WORK/cargo"
export CARGO_BUILD_JOBS=2
python3 -m venv "$VSR_QUALIFY_WORK/cli"
"$VSR_QUALIFY_WORK/cli/bin/pip" install --index-url https://pypi.org/simple \
  --no-cache-dir --upgrade pip==26.2.1
"$VSR_QUALIFY_WORK/cli/bin/pip" install --index-url https://pypi.org/simple \
  --no-cache-dir ./src/vllm-sr
python3 -m venv "$VSR_QUALIFY_WORK/provider"
"$VSR_QUALIFY_WORK/provider/bin/pip" install --index-url https://pypi.org/simple \
  --no-cache-dir --upgrade pip==26.2.1
"$VSR_QUALIFY_WORK/provider/bin/pip" install --index-url https://pypi.org/simple \
  --no-cache-dir \
  -r tools/test/services/provider-mocker/requirements.txt
python3 tools/test/smoke/riscv64-standalone.py \
  --image semantic-router-router:public \
  --cli "$VSR_QUALIFY_WORK/cli/bin/vllm-sr" \
  --provider-python "$VSR_QUALIFY_WORK/provider/bin/python" \
  --output "$VSR_QUALIFY_WORK/smoke"
```

Python 3.11+ is required for the provider fixture. Keep its environment separate
from the CLI because their dependency constraints differ. Docker access may
require administrator privileges; apply the same Docker connection environment
to the build and smoke commands. Output paths must be absolute and writable.
Source-built Python dependencies also need the host C compiler and Python
development headers. Building those dependencies is expected when a public
wheel is unavailable; the flow does not require a developer-prepared wheelhouse
or a third-party wheel index. Save `pip freeze` from both environments alongside
the acceptance report to identify the dependency versions actually tested.
Upgrade pip before resolving dependencies: the pip 24.0 bundled with some
Python 3.12 environments does not recognize RISC-V manylinux wheel tags. That
can trigger unnecessary source builds of packages whose public wheels exist.
The commands above pin the tested pip release rather than depending on an
unspecified latest installer.

The smoke creates uniquely named temporary networks and uses the maintained
`vllm-sr serve` flow, not a separate Router startup path. It starts the repository's
deterministic provider fixture, checks keyword/default model selection with both
buffered and SSE requests, stops and recreates the stack, and repeats the checks.
On the second start it removes the original input from its original path and
uses the persisted runtime configuration, so regeneration from the original
source cannot produce a false persistence pass. It verifies the actual running
image ID, SSE event framing/media type and unchanged runtime configuration,
then stops its own stack and removes its backend network. Existing stacks are
not selected for cleanup. `--port-offset` changes the default 12000 offset if
those host ports are in use.

This is a transport/lifecycle qualification without model inference. The local
Domain model, real generation backend, Dashboard, Helm and Operator each need
their own integration tests; this smoke does not imply those passed.

## Dashboard static resources

The backend build accepts the public SDK as `GO_IMAGE`; a Python-capable Debian
runtime can be supplied through `RUNTIME_IMAGE`. The build runner's Dashboard
mode currently uses the SDK as its Python-capable runtime, which is larger than
a dedicated runtime image and is not a final optimized release layout.

On a native x86/ARM builder, the existing `frontend-builder` stage can produce
the CPU-independent JS/CSS/DSL-WASM resources. Supply an independently built,
verified static-assets image through `--frontend-assets` when using
`--component dashboard`. Its required path is
`/workspace/dashboard/frontend/dist`. See [Dashboard build inputs](../../../dashboard/README.md).
The static image is an explicit input, not an implicit local cache. A complete
cross-host static-assets packaging command and full native model-runtime wheel
lock are not supplied by this core Router build recipe.
