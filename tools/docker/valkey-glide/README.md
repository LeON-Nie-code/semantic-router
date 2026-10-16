# GLIDE FFI for Linux RISC-V64

The Router's Go client is pinned to Valkey GLIDE v2.3.0. That module carries
prebuilt Rust FFI archives for amd64 and arm64, but none for riscv64. A
RISC-V build therefore needs the actual `libglide_ffi.a` from the same source
version, supplied to the final Go linker.

`../build-valkey-glide-riscv64.sh` downloads the official v2.3.0 source,
verifies its SHA-256, applies this directory's dependency lock and builds
`riscv64gc-unknown-linux-gnu`. It checks the ELF machine of the archive's
objects before copying the result to the output directory. The script also
checks that the Go module still uses v2.3.0; update the source pin and lock
together when upgrading that client. GLIDE's source is Apache-2.0 licensed.

## Native build

Requirements: Rust 1.90 with the RISC-V GNU standard library, GCC/G++, CMake,
Make, pkg-config, protobuf compiler 3.20 or newer, curl, tar and GNU binutils.
The hardware qualification used protobuf 3.21.12 and Go 1.25.14.

From the repository root:

```bash
bash tools/docker/build-valkey-glide-riscv64.sh \
  --output "$PWD/.agent-harness/riscv64/lib"

libdir="$PWD/.agent-harness/riscv64/lib"
mkdir -p bin
cd src/semantic-router
CGO_ENABLED=1 go build \
  -ldflags="-extldflags=-L${libdir}" \
  -o ../../bin/router-riscv64 ./cmd
```

Supplying the search path through the final linker keeps it out of unrelated CGO compilation
flags. A model-runtime deployment with an attached endpoint can then run
the Router standalone without installing PyTorch on that host.

## Cross build

Install the Rust target and a GNU RISC-V C/C++ toolchain. The script defaults
to `riscv64-linux-gnu-gcc` and `riscv64-linux-gnu-g++` on other hosts; the
standard Cargo target linker and `CC_…`/`CXX_…` environment variables override
them. Set `CARGO_TARGET_DIR` to reuse a build cache.

An already downloaded archive may be passed with `--archive PATH`; it goes
through the same fixed checksum check. For a Go cross build, also set
`GOOS=linux GOARCH=riscv64 CC=riscv64-linux-gnu-gcc` in the command above.

## Qualification boundary

Building and linking the library proves the dependency's architecture and
ABI are usable. Each enabled Valkey cache, memory or vector-store operation
still needs functional tests against the corresponding server and modules.
Keep the source archive hash, lock file, toolchain version, library hash and
final Router hash with the hardware test results.
