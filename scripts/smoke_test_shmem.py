#!/usr/bin/env python3
"""Compile and run a two-rank OpenSHMEM smoke test."""

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


def unsupported(message: str) -> None:
    if os.environ.get("MPI_EXTENSIONS_ALLOW_UNSUPPORTED_SHMEM") == "1":
        print("OpenSHMEM smoke test unsupported on this package")
        raise SystemExit(0)
    die(message)


def shmem_env(prefix: Path) -> dict[str, str]:
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
            "OSHRUN_LAUNCHER": env.get("OSHRUN_LAUNCHER", str(prefix / "bin" / "mpirun")),
            "SHMEM_OFI_PROVIDER": env.get("SHMEM_OFI_PROVIDER", "sockets"),
        }
    )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", help="Open MPI install prefix")
    args = parser.parse_args()

    prefix = Path(args.prefix).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[1]

    wrapper = prefix / "bin" / "oshcc"
    if not os.access(wrapper, os.X_OK):
        wrapper = prefix / "bin" / "shmemcc"
    if not os.access(wrapper, os.X_OK):
        unsupported(f"oshcc/shmemcc not found under {prefix / 'bin'}")

    runner = prefix / "bin" / "oshrun"
    if not os.access(runner, os.X_OK):
        unsupported(f"oshrun not found or not executable: {runner}")

    env = shmem_env(prefix)
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "shmem_hello"
        subprocess.run([str(wrapper), str(repo_root / "examples" / "shmem_hello.c"), "-o", str(binary)], env=env, check=True)
        completed = subprocess.run(
            [str(runner), "--oversubscribe", "-np", "2", str(binary)],
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )

    lines = set(completed.stdout.splitlines())
    if "SHMEM PE 0 of 2" not in lines:
        die("SHMEM PE 0 output missing")
    if "SHMEM PE 1 of 2" not in lines:
        die("SHMEM PE 1 output missing")

    print("OpenSHMEM smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
