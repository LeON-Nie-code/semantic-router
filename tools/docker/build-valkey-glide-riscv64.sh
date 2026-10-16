#!/usr/bin/env bash
# Build the real GLIDE FFI for the Go module's pinned client, without stubs.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
source_archive=
output=
while (($#)); do
  case "$1" in
    --archive|--output)
      (($# >= 2)) || { echo "missing value for $1" >&2; exit 2; }
      if [[ "$1" == --archive ]]; then source_archive=$2; else output=$2; fi
      shift 2 ;;
    *) echo "usage: bash $0 [--archive SOURCE.tar.gz] --output DIRECTORY" >&2; exit 2 ;;
  esac
done
[[ -n "$output" ]] || { echo "--output is required" >&2; exit 2; }
version=$(awk '$1 == "github.com/valkey-io/valkey-glide/go/v2" {print $2}' "$root/src/semantic-router/go.mod")
[[ "$version" == v2.3.0 ]] || { echo "GLIDE source and lock must be updated for Go client $version" >&2; exit 1; }
if [[ -n "$source_archive" && ! -f "$source_archive" ]]; then
  echo "source archive not found: $source_archive" >&2
  exit 1
fi
scratch=$(mktemp -d "${TMPDIR:-/tmp}/vsr-glide-riscv64.XXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
if [[ -z "$source_archive" ]]; then
  source_archive="$scratch/source.tar.gz"
  curl --fail --location --retry 3 --connect-timeout 20 \
    https://codeload.github.com/valkey-io/valkey-glide/tar.gz/refs/tags/v2.3.0 \
    -o "$source_archive"
fi
checksum=$(sha256sum "$source_archive" | awk '{print $1}')
if [[ "$checksum" != 861a5345b423206fa9090925767b3f9524e4bf135b851beaa6ccf41eb4153e53 ]]; then
  echo "GLIDE source checksum mismatch" >&2
  exit 1
fi
mkdir -p "$scratch/source"
tar -xzf "$source_archive" -C "$scratch/source" --strip-components=1
cp "$root/tools/docker/valkey-glide/riscv64-Cargo.lock" "$scratch/source/ffi/Cargo.lock"
export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-$scratch/target}
mkdir -p "$CARGO_TARGET_DIR"
CARGO_TARGET_DIR=$(cd "$CARGO_TARGET_DIR" && pwd)
export CARGO_TARGET_DIR
if [[ "$(uname -m)" != riscv64 ]]; then
  export CARGO_TARGET_RISCV64GC_UNKNOWN_LINUX_GNU_LINKER=${CARGO_TARGET_RISCV64GC_UNKNOWN_LINUX_GNU_LINKER:-riscv64-linux-gnu-gcc}
  export CC_riscv64gc_unknown_linux_gnu=${CC_riscv64gc_unknown_linux_gnu:-riscv64-linux-gnu-gcc}
  export CXX_riscv64gc_unknown_linux_gnu=${CXX_riscv64gc_unknown_linux_gnu:-riscv64-linux-gnu-g++}
fi
(
  cd "$scratch/source/ffi"
  cargo build --locked --release --target riscv64gc-unknown-linux-gnu
)
library="$CARGO_TARGET_DIR/riscv64gc-unknown-linux-gnu/release/libglide_ffi.a"
machines=$(LC_ALL=C readelf -h "$library" | awk '/Machine:/ {sub(/^.*Machine:[[:space:]]*/, ""); print}' | sort -u)
[[ "$machines" == RISC-V ]] || { echo "GLIDE archive does not contain only RISC-V objects: $machines" >&2; exit 1; }
mkdir -p "$output"
install -m 0644 "$library" "$output/libglide_ffi.a"
sha256sum "$output/libglide_ffi.a"
