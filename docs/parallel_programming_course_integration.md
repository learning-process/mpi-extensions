# parallel_programming_course integration

`parallel_programming_course` should consume this repository as a binary
supplier only. Do not install Homebrew or apt Open MPI in the course CI job.

## GitHub Actions usage

Install the current nightly package before configuring the course build:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/learning-process/mpi-extensions/main/scripts/install_from_nightly_release.sh \
  -o /tmp/install_mpi_extensions.py
python3 /tmp/install_mpi_extensions.py \
  --repo learning-process/mpi-extensions \
  --platform auto \
  --prefix "$PWD/_deps/mpi-extensions-openmpi"

export MPI_EXTENSIONS_HOME="$PWD/_deps/mpi-extensions-openmpi"
export MPI_HOME="$MPI_EXTENSIONS_HOME"
export OPAL_PREFIX="$MPI_EXTENSIONS_HOME"
export PATH="$MPI_EXTENSIONS_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$MPI_EXTENSIONS_HOME/lib:${LD_LIBRARY_PATH:-}"
export DYLD_LIBRARY_PATH="$MPI_EXTENSIONS_HOME/lib:${DYLD_LIBRARY_PATH:-}"

cmake -S . -B build
cmake --build build --parallel
```

The package is intended to be discovered by CMake through the Open MPI wrapper
compilers in `bin/`. Keep `PATH` pointed at the package before calling CMake so
`find_package(MPI)` resolves `mpicc` and `mpicxx` from this archive.

## Platform behavior

Linux packages are built with MPI and OpenSHMEM/OSHMEM enabled, bundle the UCX
runtime libraries needed by OpenSHMEM, and are validated with two-rank C smoke
tests.

Open MPI 5.0.x marks OSHMEM as Linux-only in its configure logic, so macOS
packages provide MPI tooling only. Their manifest records the OpenSHMEM smoke
test as `unsupported`.

## Nightly channel

Only the `nightly` pre-release is public. Scheduled and manual workflow runs
replace assets in that same release, including the archive, SHA256 checksum,
and JSON manifest for every platform.
