#!/usr/bin/env python3
"""Compile and run a two-rank MPI smoke test."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def mpi_env(prefix: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "OPAL_PREFIX": str(prefix),
            "OPAL_BINDIR": str(prefix / "bin"),
            "OPAL_LIBDIR": str(prefix / "lib"),
            "OPAL_INCLUDEDIR": str(prefix / "include"),
            "OPAL_PKGDATADIR": str(prefix / "share" / "openmpi"),
            "PATH": f"{prefix / 'bin'}{os.pathsep}{env.get('PATH', '')}",
            "LD_LIBRARY_PATH": f"{prefix / 'lib'}{os.pathsep}{env.get('LD_LIBRARY_PATH', '')}",
            "DYLD_LIBRARY_PATH": f"{prefix / 'lib'}{os.pathsep}{env.get('DYLD_LIBRARY_PATH', '')}",
            "OMPI_ALLOW_RUN_AS_ROOT": env.get("OMPI_ALLOW_RUN_AS_ROOT", "1"),
            "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM": env.get("OMPI_ALLOW_RUN_AS_ROOT_CONFIRM", "1"),
            "OMPI_MCA_shmem": env.get("OMPI_MCA_shmem", "mmap"),
        }
    )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", help="Open MPI install prefix")
    args = parser.parse_args()

    prefix = Path(args.prefix).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[1]
    mpicc = prefix / "bin" / "mpicc"
    if not os.access(mpicc, os.X_OK):
        die(f"mpicc not found or not executable: {mpicc}")

    runner = prefix / "bin" / "mpirun"
    if not os.access(runner, os.X_OK):
        runner = prefix / "bin" / "mpiexec"
    if not os.access(runner, os.X_OK):
        die(f"mpirun/mpiexec not found under {prefix / 'bin'}")

    env = mpi_env(prefix)
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "mpi_hello"
        subprocess.run([str(mpicc), str(repo_root / "examples" / "mpi_hello.c"), "-o", str(binary)], env=env, check=True)
        completed = subprocess.run(
            [str(runner), "--oversubscribe", "-np", "2", str(binary)],
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )

    lines = set(completed.stdout.splitlines())
    if "MPI rank 0 of 2" not in lines:
        die("MPI rank 0 output missing")
    if "MPI rank 1 of 2" not in lines:
        die("MPI rank 1 output missing")

    print("MPI smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
