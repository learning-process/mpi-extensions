#!/usr/bin/env python3
"""Build and install the pinned Open MPI submodule."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def abs_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def safe_remove(path: Path) -> None:
    path = path.resolve()
    if str(path) in {"/", ""}:
        die(f"refusing to remove unsafe path: {path}")
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def detect_work_root(repo_root: Path) -> Path:
    explicit = os.environ.get("MPI_EXTENSIONS_WORK_ROOT")
    if explicit:
        return abs_path(explicit)

    workspace_root = repo_root.parent
    if any((workspace_root / name).is_dir() for name in ("build", "install", "ccache")):
        return workspace_root
    return repo_root


def detect_jobs() -> int:
    return os.cpu_count() or 2


def setup_ccache(mode: str, build_dir: Path, repo_root: Path, work_root: Path, env: dict[str, str]) -> None:
    if mode == "off":
        return

    ccache_bin = shutil.which("ccache")
    if ccache_bin is None:
        if mode == "on":
            die("ccache was requested but was not found in PATH")
        print("ccache not found; building without compiler cache")
        return

    if not env.get("CCACHE_DIR") and (work_root / "ccache").is_dir():
        env["CCACHE_DIR"] = str(work_root / "ccache")
    if env.get("CCACHE_DIR"):
        Path(env["CCACHE_DIR"]).mkdir(parents=True, exist_ok=True)

    env.setdefault("CCACHE_BASEDIR", str(repo_root))
    env.setdefault("CCACHE_NOHASHDIR", "true")

    shim_dir = build_dir / ".ccache-wrappers"
    shim_dir.mkdir(parents=True, exist_ok=True)
    for compiler in ("cc", "c++", "gcc", "g++", "clang", "clang++"):
        link = shim_dir / compiler
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(ccache_bin)

    env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    print(f"Using ccache through compiler shims in {shim_dir}")
    if env.get("CCACHE_DIR"):
        print(f"  CCACHE_DIR={env['CCACHE_DIR']}")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    work_root = detect_work_root(repo_root)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, help="Install prefix for Open MPI")
    parser.add_argument(
        "--build-dir",
        default=str(work_root / "build" / "openmpi"),
        help="Out-of-source build directory",
    )
    parser.add_argument(
        "--source-dir",
        default=str(repo_root / "external" / "ompi"),
        help="Open MPI source checkout",
    )
    parser.add_argument("--jobs", type=int, default=detect_jobs(), help="Parallel build jobs")
    parser.add_argument(
        "--enable-shmem",
        choices=("auto", "required", "disabled"),
        default="auto",
        help="OpenSHMEM/OSHMEM build mode",
    )
    parser.add_argument(
        "--ccache",
        choices=("auto", "on", "off"),
        default=os.environ.get("MPI_EXTENSIONS_CCACHE", "auto"),
        help="Use ccache via compiler shims",
    )
    parser.add_argument(
        "--configure-arg",
        action="append",
        default=[],
        help="Extra argument passed to Open MPI configure",
    )
    args = parser.parse_args()

    if args.jobs <= 0:
        die("--jobs must be a positive integer")

    prefix = abs_path(args.prefix)
    build_dir = abs_path(args.build_dir)
    source_dir = abs_path(args.source_dir)
    if not source_dir.is_dir():
        die(f"Open MPI source directory not found: {source_dir}")

    host_os = os.uname().sysname
    shmem_mode = args.enable_shmem
    if shmem_mode == "auto":
        shmem_mode = "required" if host_os == "Linux" else "disabled"
    if shmem_mode == "required" and host_os != "Linux":
        die(f"Open MPI 5.0.x OpenSHMEM/OSHMEM support is Linux-only; cannot require SHMEM on {host_os}")

    for tool in ("perl", "make"):
        if shutil.which(tool) is None:
            die(f"required tool not found: {tool}")

    env = os.environ.copy()
    safe_remove(build_dir)
    safe_remove(prefix)
    build_dir.mkdir(parents=True, exist_ok=True)
    prefix.mkdir(parents=True, exist_ok=True)
    setup_ccache(args.ccache, build_dir, repo_root, work_root, env)

    if not (source_dir / "configure").is_file():
        print(f"Bootstrapping Open MPI in {source_dir}")
        run(["./autogen.pl"], cwd=source_dir, env=env)

    configure_args = [
        f"--prefix={prefix}",
        "--disable-dependency-tracking",
        "--disable-mpi-fortran",
        "--disable-oshmem-fortran",
        "--enable-prte-prefix-by-default",
        "--with-libevent=internal",
        "--with-hwloc=internal",
        "--with-pmix=internal",
        "--with-prrte=internal",
        "--without-cuda",
        "--without-ofi",
    ]
    if shmem_mode == "required":
        configure_args.append("--with-ucx")
    else:
        configure_args.append("--without-ucx")
    configure_args.append("--enable-oshmem" if shmem_mode == "required" else "--disable-oshmem")
    configure_args.extend(args.configure_arg)

    print("Configuring Open MPI")
    print(f"  source: {source_dir}")
    print(f"  build:  {build_dir}")
    print(f"  prefix: {prefix}")
    print(f"  shmem:  {shmem_mode}")

    run([str(source_dir / "configure"), *configure_args], cwd=build_dir, env=env)
    run(["make", "-j", str(args.jobs)], cwd=build_dir, env=env)
    run(["make", "install"], cwd=build_dir, env=env)

    for tool in ("mpicc", "mpicxx", "mpirun", "mpiexec"):
        if not os.access(prefix / "bin" / tool, os.X_OK):
            die(f"expected MPI tool was not installed: {prefix / 'bin' / tool}")

    if shmem_mode == "required":
        if not os.access(prefix / "bin" / "oshrun", os.X_OK):
            die(f"expected OpenSHMEM launcher was not installed: {prefix / 'bin' / 'oshrun'}")
        if not any(os.access(prefix / "bin" / tool, os.X_OK) for tool in ("oshcc", "shmemcc")):
            die("expected OpenSHMEM C wrapper was not installed: oshcc or shmemcc")

    print(f"Open MPI installed in {prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
