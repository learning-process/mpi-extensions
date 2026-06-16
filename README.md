# mpi-extensions

`mpi-extensions` supplies Open MPI binary packages for
[`parallel_programming_course`](https://github.com/learning-process/parallel_programming_course).
The packages are built from the Open MPI git submodule at `external/ompi` and
are intended for rootless CI use: download, extract, add `bin/` to `PATH`, and
let CMake discover `mpicc` and `mpicxx`.

This repository publishes only one public binary channel: the moving `nightly`
pre-release named `Nightly Open MPI Extensions`. Stable versioned releases are
not supported here.

## Open MPI pin

Open MPI is included as a git submodule, not vendored source:

- URL: `https://github.com/open-mpi/ompi`
- Path: `external/ompi`
- Ref: `v5.0.10`
- Commit: `0d48030d410ae8f56790933135b28be1b3920ba1`

The same data is recorded in `manifests/openmpi-version.json`.

Linux packages are configured with MPI and OpenSHMEM/OSHMEM enabled and bundle
the UCX runtime libraries used by OpenSHMEM. Open MPI 5.0.x marks OSHMEM as
Linux-only in its configure logic, so macOS packages are MPI-only and their
package manifests record OpenSHMEM validation as `unsupported`.

## Nightly assets

The workflow publishes these archive assets to the `nightly` pre-release:

- `mpi-extensions-openmpi-v5.0.10-linux-x86_64.tar.gz`
- `mpi-extensions-openmpi-v5.0.10-macos-arm64.tar.gz`

Each archive is uploaded with:

- `<archive>.sha256`
- `<archive>.manifest.json`

Archives unpack under a single top-level directory and contain `bin/`, `lib/`,
`include/`, `share/`, `manifest.json`, and `LICENSES/`.

## Install from the nightly release

For local use from this repository:

```bash
./scripts/install_from_nightly_release.sh \
  --repo learning-process/mpi-extensions \
  --platform auto \
  --prefix "$PWD/_deps/mpi-extensions-openmpi"
```

The installer downloads only the `nightly` pre-release, verifies the SHA256
checksum, extracts into the requested prefix, and prints the environment values
needed by downstream builds.

From another repository, such as `parallel_programming_course`, fetch the
installer script directly from this repository:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/learning-process/mpi-extensions/main/scripts/install_from_nightly_release.sh \
  -o /tmp/install_mpi_extensions.py
python3 /tmp/install_mpi_extensions.py \
  --repo learning-process/mpi-extensions \
  --platform auto \
  --prefix "$PWD/_deps/mpi-extensions-openmpi"
```

Recommended `parallel_programming_course` CI setup:

```bash
export MPI_EXTENSIONS_HOME="$PWD/_deps/mpi-extensions-openmpi"
export MPI_HOME="$MPI_EXTENSIONS_HOME"
export OPAL_PREFIX="$MPI_EXTENSIONS_HOME"
export PATH="$MPI_EXTENSIONS_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$MPI_EXTENSIONS_HOME/lib:${LD_LIBRARY_PATH:-}"
export DYLD_LIBRARY_PATH="$MPI_EXTENSIONS_HOME/lib:${DYLD_LIBRARY_PATH:-}"

cmake -S . -B build
cmake --build build --parallel
```

Do not install Homebrew or apt Open MPI in the course workflow. The binary
package is meant to be self-contained for CMake discovery through the wrapper
compilers.

## Local build

Install the normal compiler/autotools dependencies first. On Ubuntu this
includes `build-essential`, `autoconf`, `automake`, `libtool`, `flex`, `bison`,
`pkg-config`, `ccache`, `zlib1g-dev`, and `libucx-dev`; `libucx-dev` is required
for the Linux OpenSHMEM provider. On macOS the workflow uses Homebrew packages
for `autoconf`, `automake`, `bison`, `ccache`, `flex`, `libtool`, `pkg-config`,
and `rsync`.

Initialize submodules first:

```bash
git submodule update --init --recursive
export MPI_EXTENSIONS_WORK_ROOT="$(cd .. && pwd)"
python3 -m venv "$MPI_EXTENSIONS_WORK_ROOT/build/venv"
. "$MPI_EXTENSIONS_WORK_ROOT/build/venv/bin/activate"
python -m pip install -r requirements.txt
```

Build and validate on Linux:

```bash
export CCACHE_DIR="$MPI_EXTENSIONS_WORK_ROOT/ccache"

./scripts/build_openmpi.sh \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --build-dir "$MPI_EXTENSIONS_WORK_ROOT/build/openmpi-linux" \
  --enable-shmem required \
  --ccache on
./scripts/smoke_test_mpi.sh "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
./scripts/smoke_test_shmem.sh "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
./scripts/package_openmpi.sh \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --out "$MPI_EXTENSIONS_WORK_ROOT/build/dist"
```

Build and validate on macOS:

```bash
export CCACHE_DIR="$MPI_EXTENSIONS_WORK_ROOT/ccache"

./scripts/build_openmpi.sh \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --build-dir "$MPI_EXTENSIONS_WORK_ROOT/build/openmpi-macos" \
  --enable-shmem disabled \
  --ccache on
./scripts/smoke_test_mpi.sh "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
MPI_EXTENSIONS_ALLOW_UNSUPPORTED_SHMEM=1 \
  ./scripts/smoke_test_shmem.sh "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
./scripts/package_openmpi.sh \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --out "$MPI_EXTENSIONS_WORK_ROOT/build/dist" \
  --platform macos-arm64 \
  --shmem-smoke unsupported
```

## Updating Open MPI

To move the pin:

```bash
git -C external/ompi fetch --tags origin
git -C external/ompi checkout <stable-tag-or-commit>
git -C external/ompi submodule update --init --recursive
git add external/ompi manifests/openmpi-version.json README.md
```

Update `manifests/openmpi-version.json` and this README with the exact ref and
commit. Use a stable tag or explicit commit, never a floating branch.

## Workflow

`.github/workflows/nightly-openmpi.yml` supports:

- `workflow_dispatch`
- nightly schedule at `02:37 UTC`
- push validation on `main`

Scheduled and manual runs replace the assets in the same `nightly` pre-release.
Pushes to `main` build and validate packages but do not publish release assets.
