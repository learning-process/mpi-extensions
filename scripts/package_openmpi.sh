#!/usr/bin/env python3
"""Package an Open MPI install tree into a release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def detect_platform() -> str:
    os_name = os.uname().sysname
    arch = os.uname().machine
    if os_name == "Linux" and arch in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if os_name == "Linux" and arch in {"aarch64", "arm64"}:
        return "linux-arm64"
    if os_name == "Darwin" and arch in {"arm64", "aarch64"}:
        return "macos-arm64"
    if os_name == "Darwin" and arch == "x86_64":
        return "macos-x86_64"
    die(f"unsupported platform for auto-detection: {os_name} {arch}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def copy_install_tree(prefix: Path, stage: Path) -> None:
    shutil.copytree(prefix, stage, symlinks=True)


def ldd_dependencies(path: Path) -> list[Path]:
    try:
        output = subprocess.check_output(["ldd", str(path)], stderr=subprocess.DEVNULL, text=True)
    except (OSError, subprocess.CalledProcessError):
        return []

    dependencies: list[Path] = []
    for line in output.splitlines():
        line = line.strip()
        if "=>" not in line:
            continue
        _, remainder = line.split("=>", 1)
        candidate = remainder.strip().split(" ", 1)[0]
        if candidate.startswith("/"):
            dependencies.append(Path(candidate))
    return dependencies


def bundle_linux_runtime_dependencies(stage: Path) -> list[str]:
    if os.uname().sysname != "Linux":
        return []

    wanted_prefixes = ("libucp.so", "libuct.so", "libucs.so", "libucm.so")
    probe_paths = [
        *stage.glob("lib/*.so"),
        *stage.glob("lib/*.so.*"),
        *stage.glob("bin/*"),
    ]

    bundled: dict[str, Path] = {}
    for probe in probe_paths:
        if not probe.is_file():
            continue
        for dependency in ldd_dependencies(probe):
            if dependency.name.startswith(wanted_prefixes):
                bundled[dependency.name] = dependency

    for name, source in sorted(bundled.items()):
        target = stage / "lib" / name
        if target.exists():
            continue
        shutil.copy2(source.resolve(), target)

    return sorted(bundled)


def copy_optional_license(candidates: tuple[Path, ...], destination: Path) -> None:
    for candidate in candidates:
        if candidate.is_file():
            shutil.copy2(candidate, destination)
            return


def write_launcher(stage: Path, tool: str) -> None:
    path = stage / "bin" / tool
    if not (path.exists() or path.is_symlink()):
        return

    if path.is_symlink():
        target_rel = os.readlink(path)
        path.unlink()
    else:
        real_dir = stage / "bin" / ".openmpi-real"
        real_dir.mkdir(parents=True, exist_ok=True)
        real_path = real_dir / tool
        shutil.move(str(path), real_path)
        target_rel = f".openmpi-real/{tool}"

    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
prefix="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
export OPAL_PREFIX="$prefix"
export OPAL_BINDIR="$prefix/bin"
export OPAL_LIBDIR="$prefix/lib"
export OPAL_INCLUDEDIR="$prefix/include"
export OPAL_PKGDATADIR="$prefix/share/openmpi"
export PATH="$prefix/bin:${{PATH:-}}"
export LD_LIBRARY_PATH="$prefix/lib:${{LD_LIBRARY_PATH:-}}"
export DYLD_LIBRARY_PATH="$prefix/lib:${{DYLD_LIBRARY_PATH:-}}"
target_rel="{target_rel}"
if [[ "$target_rel" = /* ]]; then
  target="$target_rel"
else
  target="$prefix/bin/$target_rel"
fi
if [[ "$(basename "$target")" == "opal_wrapper" ]]; then
  exec -a "{tool}" "$target" "$@"
fi
exec "$target" "$@"
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def platform_parts(platform: str) -> tuple[str, str]:
    if platform.startswith("linux-"):
        package_os = "linux"
    elif platform.startswith("macos-"):
        package_os = "macos"
    else:
        die(f"unsupported package platform: {platform}")

    if platform.endswith("-x86_64"):
        arch = "x86_64"
    elif platform.endswith("-arm64"):
        arch = "arm64"
    else:
        die(f"unsupported package architecture in platform: {platform}")
    return package_os, arch


def make_manifest(
    *,
    package_name: str,
    platform: str,
    package_os: str,
    arch: str,
    openmpi_git_url: str,
    openmpi_ref: str,
    openmpi_commit: str,
    archive_name: str,
    archive_sha: str | None,
    included_tools: list[str],
    bundled_runtime_libraries: list[str],
    mpi_smoke: str,
    shmem_smoke: str,
) -> dict[str, object]:
    return {
        "package_name": package_name,
        "platform": platform,
        "os": package_os,
        "arch": arch,
        "openmpi_git_url": openmpi_git_url,
        "openmpi_ref": openmpi_ref,
        "openmpi_commit": openmpi_commit,
        "build_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "archive_name": archive_name,
        "sha256": archive_sha,
        "included_tools": included_tools,
        "bundled_runtime_libraries": bundled_runtime_libraries,
        "validation": {
            "mpi_smoke_test": mpi_smoke,
            "shmem_smoke_test": shmem_smoke,
        },
    }


def write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_archive(archive: Path, tmpdir: Path, package_name: str) -> None:
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tmpdir / package_name, arcname=package_name, recursive=True)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, help="Open MPI install prefix")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--platform", default="auto", help="Package platform")
    parser.add_argument("--mpi-smoke", default="passed", help="MPI validation status")
    parser.add_argument("--shmem-smoke", default="passed", help="OpenSHMEM validation status")
    args = parser.parse_args()

    prefix = Path(args.prefix).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    if not prefix.is_dir():
        die(f"install prefix does not exist: {prefix}")

    platform = detect_platform() if args.platform == "auto" else args.platform
    package_os, arch = platform_parts(platform)

    for tool in ("mpicc", "mpicxx", "mpirun", "mpiexec"):
        if not os.access(prefix / "bin" / tool, os.X_OK):
            die(f"required tool missing: {prefix / 'bin' / tool}")

    included_tools = ["mpicc", "mpicxx", "mpirun", "mpiexec"]
    for tool in ("oshrun", "oshcc", "shmemcc"):
        if os.access(prefix / "bin" / tool, os.X_OK):
            included_tools.append(tool)

    version_manifest = json.loads((repo_root / "manifests" / "openmpi-version.json").read_text(encoding="utf-8"))
    openmpi_ref = version_manifest["openmpi_ref"]
    openmpi_repo = repo_root / "external" / "ompi"
    openmpi_git_url = (
        git_output(openmpi_repo, "config", "--get", "remote.origin.url")
        or version_manifest["openmpi_git_url"]
    )
    openmpi_commit = (
        git_output(openmpi_repo, "rev-parse", "HEAD")
        or version_manifest["openmpi_commit"]
    )

    package_name = f"mpi-extensions-openmpi-{openmpi_ref}-{platform}"
    archive_name = f"{package_name}.tar.gz"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / archive_name

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        stage = tmpdir / package_name
        copy_install_tree(prefix, stage)

        licenses = stage / "LICENSES"
        licenses.mkdir(parents=True, exist_ok=True)
        shutil.copy2(openmpi_repo / "LICENSE", licenses / "OpenMPI-LICENSE.txt")
        shutil.copy2(repo_root / "LICENSE", licenses / "mpi-extensions-LICENSE.txt")
        bundled_runtime_libraries = bundle_linux_runtime_dependencies(stage)
        if bundled_runtime_libraries:
            copy_optional_license(
                (
                    Path("/usr/share/doc/libucx0/copyright"),
                    Path("/usr/share/doc/libucx-dev/copyright"),
                    Path("/usr/share/doc/ucx/copyright"),
                ),
                licenses / "UCX-copyright.txt",
            )

        for tool in ("mpicc", "mpicxx", "mpirun", "mpiexec", "oshrun", "oshcc", "shmemcc"):
            write_launcher(stage, tool)

        internal_manifest = make_manifest(
            package_name=package_name,
            platform=platform,
            package_os=package_os,
            arch=arch,
            openmpi_git_url=openmpi_git_url,
            openmpi_ref=openmpi_ref,
            openmpi_commit=openmpi_commit,
            archive_name=archive_name,
            archive_sha=None,
            included_tools=included_tools,
            bundled_runtime_libraries=bundled_runtime_libraries,
            mpi_smoke=args.mpi_smoke,
            shmem_smoke=args.shmem_smoke,
        )
        write_json(stage / "manifest.json", internal_manifest)

        create_archive(archive_path, tmpdir, package_name)

    archive_sha = sha256_file(archive_path)
    (out_dir / f"{archive_name}.sha256").write_text(f"{archive_sha}  {archive_name}\n", encoding="utf-8")
    release_manifest = make_manifest(
        package_name=package_name,
        platform=platform,
        package_os=package_os,
        arch=arch,
        openmpi_git_url=openmpi_git_url,
        openmpi_ref=openmpi_ref,
        openmpi_commit=openmpi_commit,
        archive_name=archive_name,
        archive_sha=archive_sha,
        included_tools=included_tools,
        bundled_runtime_libraries=bundled_runtime_libraries,
        mpi_smoke=args.mpi_smoke,
        shmem_smoke=args.shmem_smoke,
    )
    write_json(out_dir / f"{archive_name}.manifest.json", release_manifest)

    print(f"Created {archive_path}")
    print(f"Created {archive_path}.sha256")
    print(f"Created {archive_path}.manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
