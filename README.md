# mpi-extensions

`mpi-extensions` supplies Open MPI binary packages for
[`parallel_programming_course`](https://github.com/learning-process/parallel_programming_course).
The packages are built from pinned git submodules and are intended for rootless
CI use: download, extract, and pass the unpacked package prefix to the course
CMake configure step.

This repository publishes only one public binary channel: the moving `main`
pre-release named `Main Open MPI Extensions`. It is replaced only after
successful builds from the `main` branch. Stable versioned releases and
scheduled nightly releases are not supported here.

## Source Pins

Open MPI and Sandia OpenSHMEM/SOS are included as git submodules, not vendored
source:

- URL: `https://github.com/open-mpi/ompi`
- Path: `external/ompi`
- Ref: `v5.0.10`
- Commit: `0d48030d410ae8f56790933135b28be1b3920ba1`

- URL: `https://github.com/Sandia-OpenSHMEM/SOS.git`
- Path: `external/sos`
- Ref: `v1.5.3`
- Commit: `45e6e7eb1a9b099a418237a9e677eb6603222c84`

The same data is recorded in `manifests/openmpi-version.json` and
`manifests/sos-version.json`.

Linux packages are configured with MPI and Open MPI OSHMEM/OpenSHMEM enabled
and bundle the UCX runtime libraries used by OpenSHMEM. Open MPI 5.0.x marks
OSHMEM as Linux-only in its configure logic, so macOS packages use Open MPI for
MPI and Sandia OpenSHMEM/SOS for SHMEM. SOS is built against the package's Open
MPI PMIx/hwloc install and libfabric/OFI, then packaged with the needed runtime
libraries for rootless CI use.

## Main Assets

The workflow publishes these archive assets to the `main` pre-release:

- `mpi-extensions-openmpi-v5.0.10-linux-x86_64.tar.gz`
- `mpi-extensions-openmpi-v5.0.10-macos-arm64.tar.gz`

Each archive is uploaded with:

- `<archive>.sha256`
- `<archive>.manifest.json`

Archives unpack under a single top-level directory and contain `bin/`, `lib/`,
`include/`, `share/`, `manifest.json`, and `LICENSES/`.

## Install From The Main Release

For local use from this repository:

```bash
./scripts/install_from_main_release.py \
  --repo learning-process/mpi-extensions \
  --platform auto \
  --prefix "$PWD/_deps/mpi-extensions-openmpi"
```

The installer downloads only the `main` pre-release, verifies the SHA256
checksum, extracts into the requested prefix, and prints environment values for
manual debugging.

From another repository, such as `parallel_programming_course`, fetch the
installer script directly from this repository:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/learning-process/mpi-extensions/main/scripts/install_from_main_release.py \
  -o /tmp/install_mpi_extensions.py
python3 /tmp/install_mpi_extensions.py \
  --repo learning-process/mpi-extensions \
  --platform auto \
  --prefix "$PWD/_deps/mpi-extensions-openmpi"
```

Recommended `parallel_programming_course` CI setup:

```bash
MPI_EXTENSIONS_HOME="$PWD/_deps/mpi-extensions-openmpi"
cmake -S . -B build -D PPC_MPI_EXTENSIONS_HOME="$MPI_EXTENSIONS_HOME"
cmake --build build --parallel
```

Do not install Homebrew or apt Open MPI in the course workflow. The course CMake
configuration must receive `PPC_MPI_EXTENSIONS_HOME`; it then points MPI
discovery and runtime setup at this package.

## Local build

All repository helper scripts in `scripts/` are Python executables with `.py`
extensions.

Install the normal compiler/autotools dependencies first. On Ubuntu this
includes `build-essential`, `autoconf`, `automake`, `libtool`, `flex`, `bison`,
`pkg-config`, `ccache`, `zlib1g-dev`, and `libucx-dev`; `libucx-dev` is required
for the Linux OpenSHMEM provider. On macOS the workflow uses Homebrew packages
for `autoconf`, `automake`, `bison`, `ccache`, `flex`, `libfabric`, `libtool`,
`pkg-config`, and `rsync`.

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

./scripts/build_openmpi.py \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --build-dir "$MPI_EXTENSIONS_WORK_ROOT/build/openmpi-linux" \
  --enable-shmem required \
  --ccache on
./scripts/smoke_test_mpi.py "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
./scripts/smoke_test_shmem.py "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
./scripts/package_openmpi.py \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --out "$MPI_EXTENSIONS_WORK_ROOT/build/dist"
```

Build and validate on macOS:

```bash
export CCACHE_DIR="$MPI_EXTENSIONS_WORK_ROOT/ccache"

./scripts/build_openmpi.py \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --build-dir "$MPI_EXTENSIONS_WORK_ROOT/build/openmpi-macos" \
  --enable-shmem disabled \
  --ccache on
./scripts/build_sos_shmem.py \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --build-dir "$MPI_EXTENSIONS_WORK_ROOT/build/sos-macos" \
  --mpi-prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --ccache on
./scripts/smoke_test_mpi.py "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
./scripts/smoke_test_shmem.py "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi"
./scripts/package_openmpi.py \
  --prefix "$MPI_EXTENSIONS_WORK_ROOT/install/openmpi" \
  --out "$MPI_EXTENSIONS_WORK_ROOT/build/dist" \
  --platform macos-arm64 \
  --shmem-smoke passed
```

## Updating Pins

To move the Open MPI pin:

```bash
git -C external/ompi fetch --tags origin
git -C external/ompi checkout <stable-tag-or-commit>
git -C external/ompi submodule update --init --recursive
git add external/ompi manifests/openmpi-version.json README.md
```

To move the SOS pin:

```bash
git -C external/sos fetch --tags origin
git -C external/sos checkout <stable-tag-or-commit>
git -C external/sos submodule update --init --recursive
git add external/sos manifests/sos-version.json README.md
```

Update the matching manifest and this README with the exact ref and commit. Use
a stable tag or explicit commit, never a floating branch.

## Workflow

`.github/workflows/main-openmpi.yml` supports:

- `workflow_dispatch`
- pull request validation targeting `main`
- push builds on `main`

Pull requests build and validate packages but do not publish. Pushes to `main`
replace the assets in the same `main` pre-release after successful builds. A
manual run from the `main` branch follows the same publish path.
