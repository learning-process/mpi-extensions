# parallel_programming_course integration

`parallel_programming_course` should consume this repository as a binary
supplier only. Do not install Homebrew or apt Open MPI in the course CI job.

## GitHub Actions usage

Install the current nightly package before configuring the course build:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/learning-process/mpi-extensions/main/scripts/install_from_nightly_release.py \
  -o /tmp/install_mpi_extensions.py
python3 /tmp/install_mpi_extensions.py \
  --repo learning-process/mpi-extensions \
  --platform auto \
  --prefix "$PWD/_deps/mpi-extensions-openmpi"

MPI_EXTENSIONS_HOME="$PWD/_deps/mpi-extensions-openmpi"
cmake -S . -B build -D PPC_MPI_EXTENSIONS_HOME="$MPI_EXTENSIONS_HOME"
cmake --build build --parallel
```

The package is intended to be passed to course CMake through
`PPC_MPI_EXTENSIONS_HOME`. The course configuration then points MPI discovery at
the wrapper compilers in the package and writes the runtime environment needed
by `scripts/run_tests.py`.

## Platform behavior

Linux packages are built with MPI and Open MPI OSHMEM/OpenSHMEM enabled,
bundle the UCX runtime libraries needed by OpenSHMEM, and are validated with
two-rank C smoke tests.

Open MPI 5.0.x marks OSHMEM as Linux-only in its configure logic, so macOS
packages provide MPI through Open MPI and SHMEM through Sandia OpenSHMEM/SOS
built against the same Open MPI PMIx/hwloc prefix and libfabric/OFI. macOS
packages are also validated with two-rank MPI and SHMEM C smoke tests.

## Nightly channel

Only the `nightly` pre-release is public. Scheduled and manual workflow runs
replace assets in that same release, including the archive, SHA256 checksum,
and JSON manifest for every platform.
