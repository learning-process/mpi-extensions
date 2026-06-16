#!/usr/bin/env python3
"""Build Sandia OpenSHMEM/SOS against an existing Open MPI install prefix."""

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


def command_output(command: list[str]) -> str | None:
    try:
        return subprocess.check_output(command, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def detect_libfabric_prefix() -> Path:
    explicit = os.environ.get("LIBFABRIC_PREFIX")
    if explicit:
        return abs_path(explicit)

    if os.uname().sysname == "Darwin":
        brew = shutil.which("brew")
        if brew:
            output = command_output([brew, "--prefix", "libfabric"])
            if output:
                return abs_path(output)

    pkg_config = shutil.which("pkg-config")
    if pkg_config:
        output = command_output([pkg_config, "--variable=prefix", "libfabric"])
        if output:
            return abs_path(output)

    return Path("/usr")


def ensure_sos_submodules(source_dir: Path) -> None:
    if not (source_dir / ".git").exists():
        return
    if (source_dir / "modules" / "tests-sos" / "Makefile.am").is_file():
        return
    run(["git", "-C", str(source_dir), "submodule", "update", "--init", "--recursive"])


def copy_source(source_dir: Path, destination: Path) -> None:
    rsync = shutil.which("rsync")
    if rsync:
        run([rsync, "-a", "--delete", "--exclude=.git", f"{source_dir}/", f"{destination}/"])
        return

    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_dir, destination, ignore=shutil.ignore_patterns(".git"))


def prepend_env_path(env: dict[str, str], name: str, *paths: Path) -> None:
    values = [str(path) for path in paths if path.exists()]
    if not values:
        return
    current = env.get(name)
    env[name] = os.pathsep.join([*values, current] if current else values)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    work_root = detect_work_root(repo_root)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, help="Install prefix where SOS should be installed")
    parser.add_argument(
        "--build-dir",
        default=str(work_root / "build" / "sos"),
        help="Build directory for the SOS source copy and object tree",
    )
    parser.add_argument(
        "--source-dir",
        default=str(repo_root / "external" / "sos"),
        help="Sandia OpenSHMEM/SOS source checkout",
    )
    parser.add_argument(
        "--mpi-prefix",
        help="Open MPI prefix that provides mpirun, PMIx, and hwloc; defaults to --prefix",
    )
    parser.add_argument(
        "--ofi-prefix",
        help="libfabric/OFI prefix; defaults to LIBFABRIC_PREFIX, Homebrew libfabric, pkg-config, or /usr",
    )
    parser.add_argument("--jobs", type=int, default=detect_jobs(), help="Parallel build jobs")
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
        help="Extra argument passed to SOS configure",
    )
    args = parser.parse_args()

    if args.jobs <= 0:
        die("--jobs must be a positive integer")

    prefix = abs_path(args.prefix)
    mpi_prefix = abs_path(args.mpi_prefix or args.prefix)
    build_dir = abs_path(args.build_dir)
    source_dir = abs_path(args.source_dir)
    ofi_prefix = abs_path(args.ofi_prefix) if args.ofi_prefix else detect_libfabric_prefix()

    if not source_dir.is_dir():
        die(f"SOS source directory not found: {source_dir}")
    if not prefix.is_dir():
        die(f"install prefix does not exist: {prefix}")
    if not os.access(mpi_prefix / "bin" / "mpirun", os.X_OK):
        die(f"Open MPI launcher not found: {mpi_prefix / 'bin' / 'mpirun'}")
    if not (mpi_prefix / "include" / "pmix.h").is_file():
        die(f"PMIx headers not found under MPI prefix: {mpi_prefix / 'include'}")
    if not any((mpi_prefix / "lib").glob("libpmix*")):
        die(f"PMIx library not found under MPI prefix: {mpi_prefix / 'lib'}")
    if not (ofi_prefix / "include" / "rdma" / "fabric.h").is_file():
        die(f"libfabric headers not found under OFI prefix: {ofi_prefix}")

    for tool in ("perl", "make"):
        if shutil.which(tool) is None:
            die(f"required tool not found: {tool}")

    ensure_sos_submodules(source_dir)
    env = os.environ.copy()
    safe_remove(build_dir)
    source_copy = build_dir / "src"
    object_dir = build_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    setup_ccache(args.ccache, build_dir, repo_root, work_root, env)
    copy_source(source_dir, source_copy)

    prepend_env_path(env, "PKG_CONFIG_PATH", mpi_prefix / "lib" / "pkgconfig", ofi_prefix / "lib" / "pkgconfig")
    prepend_env_path(env, "LD_LIBRARY_PATH", mpi_prefix / "lib", ofi_prefix / "lib")
    prepend_env_path(env, "DYLD_LIBRARY_PATH", mpi_prefix / "lib", ofi_prefix / "lib")

    if not (source_copy / "configure").is_file():
        print(f"Bootstrapping SOS in {source_copy}")
        run(["./autogen.sh"], cwd=source_copy, env=env)

    object_dir.mkdir(parents=True, exist_ok=True)
    configure_args = [
        f"--prefix={prefix}",
        f"--with-ofi={ofi_prefix}",
        f"--with-hwloc={mpi_prefix}",
        f"--with-pmix={mpi_prefix}",
        f"--with-oshrun-launcher={mpi_prefix / 'bin' / 'mpirun'}",
        "--disable-fortran",
        "--disable-manpages",
        *args.configure_arg,
    ]

    print("Configuring Sandia OpenSHMEM/SOS")
    print(f"  source:     {source_copy}")
    print(f"  build:      {object_dir}")
    print(f"  prefix:     {prefix}")
    print(f"  mpi-prefix: {mpi_prefix}")
    print(f"  ofi-prefix: {ofi_prefix}")

    run([str(source_copy / "configure"), *configure_args], cwd=object_dir, env=env)
    run(["make", "-j", str(args.jobs)], cwd=object_dir, env=env)
    run(["make", "install"], cwd=object_dir, env=env)

    if not os.access(prefix / "bin" / "oshcc", os.X_OK):
        die(f"expected SOS C wrapper was not installed: {prefix / 'bin' / 'oshcc'}")
    if not os.access(prefix / "bin" / "oshrun", os.X_OK):
        die(f"expected SOS launcher was not installed: {prefix / 'bin' / 'oshrun'}")
    if not (prefix / "include" / "shmem.h").is_file():
        die(f"expected OpenSHMEM header was not installed: {prefix / 'include' / 'shmem.h'}")
    if not any((prefix / "lib").glob("libsma*")):
        die(f"expected SOS runtime library was not installed under {prefix / 'lib'}")

    shmemcc = prefix / "bin" / "shmemcc"
    if not shmemcc.exists() and not shmemcc.is_symlink():
        shmemcc.symlink_to("oshcc")

    print(f"Sandia OpenSHMEM/SOS installed in {prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
