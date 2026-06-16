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


def sha256_package_tree(root: Path, *, exclude: set[Path] | None = None) -> str:
    excluded = {path.resolve() for path in (exclude or set())}
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.resolve() in excluded:
            continue
        relative = path.relative_to(root).as_posix()
        stat_result = path.lstat()
        mode = stat_result.st_mode & 0o777
        if path.is_symlink():
            digest.update(f"L {mode:o} {relative}\0{os.readlink(path)}\0".encode())
        elif path.is_dir():
            digest.update(f"D {mode:o} {relative}\0".encode())
        elif path.is_file():
            digest.update(f"F {mode:o} {relative}\0".encode())
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
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


def detect_included_tools(root: Path) -> list[str]:
    tools = ["mpicc", "mpicxx", "mpirun", "mpiexec"]
    for tool in ("oshrun", "oshcc", "shmemcc", "oshc++"):
        if os.access(root / "bin" / tool, os.X_OK):
            tools.append(tool)
    return tools


def is_sos_install(root: Path) -> bool:
    return any((root / "lib").glob("libsma*")) or (root / "lib" / "pkgconfig" / "sandia-openshmem.pc").is_file()


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


def otool_dependencies(path: Path) -> list[str]:
    try:
        output = subprocess.check_output(["otool", "-L", str(path)], stderr=subprocess.DEVNULL, text=True)
    except (OSError, subprocess.CalledProcessError):
        return []

    dependencies: list[str] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        dependencies.append(line.split(" ", 1)[0])
    return dependencies


def path_is_under(path: Path, directory: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(directory.resolve(strict=False))
        return True
    except ValueError:
        return False


def is_system_macos_dependency(dependency: str) -> bool:
    return dependency.startswith("/usr/lib/") or dependency.startswith("/System/Library/")


def macos_probe_paths(stage: Path) -> list[Path]:
    probes: list[Path] = []
    for root in (stage / "bin", stage / "lib"):
        if root.is_dir():
            probes.extend(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())
    return probes


def macos_runtime_seed_paths(stage: Path) -> list[Path]:
    seeds: list[Path] = []
    for tool in (
        "mpirun",
        "mpiexec",
        "opal_wrapper",
        "prte",
        "prted",
        "prterun",
        "oshcc",
        "oshrun",
    ):
        path = stage / "bin" / tool
        if path.is_file() and not path.is_symlink():
            seeds.append(path)
    for pattern in (
        "libmpi*.dylib",
        "libopen-pal*.dylib",
        "libpmix*.dylib",
        "libevent*.dylib",
        "libhwloc*.dylib",
        "libsma*.dylib",
    ):
        seeds.extend(path for path in (stage / "lib").glob(pattern) if path.is_file() and not path.is_symlink())
    return seeds


def bundle_macos_runtime_dependencies(stage: Path, original_prefix: Path) -> list[str]:
    if os.uname().sysname != "Darwin":
        return []

    bundled: dict[str, Path] = {}
    for _ in range(8):
        changed = False
        probes = [*macos_runtime_seed_paths(stage), *(stage / "lib" / name for name in bundled)]
        for probe in probes:
            if not probe.is_file():
                continue
            for dependency in otool_dependencies(probe):
                if not dependency.startswith("/") or is_system_macos_dependency(dependency):
                    continue
                dependency_path = Path(dependency)
                if path_is_under(dependency_path, original_prefix) or path_is_under(dependency_path, stage):
                    continue
                if not dependency_path.is_file() or ".dylib" not in dependency_path.name:
                    continue

                target = stage / "lib" / dependency_path.name
                if target.exists():
                    continue
                bundled[dependency_path.name] = dependency_path
                shutil.copy2(dependency_path.resolve(), target)
                if dependency_path.name.startswith("libfabric.") and dependency_path.name != "libfabric.dylib":
                    libfabric_link = stage / "lib" / "libfabric.dylib"
                    if not libfabric_link.exists() and not libfabric_link.is_symlink():
                        libfabric_link.symlink_to(dependency_path.name)
                changed = True
        if not changed:
            break

    return sorted(bundled)


def loader_path_reference(binary: Path, stage: Path, library_name: str) -> str:
    relative = os.path.relpath(stage / "lib" / library_name, binary.parent).replace(os.sep, "/")
    return f"@loader_path/{relative}"


def patch_macos_install_names(stage: Path) -> None:
    if os.uname().sysname != "Darwin":
        return
    if shutil.which("install_name_tool") is None:
        die("install_name_tool is required to package relocatable macOS binaries")

    library_names = {
        path.name
        for path in (stage / "lib").iterdir()
        if (path.is_file() or path.is_symlink()) and ".dylib" in path.name
    }
    if not library_names:
        return

    for path in macos_probe_paths(stage):
        dependencies = otool_dependencies(path)
        if not dependencies:
            continue

        if path.parent == stage / "lib" and ".dylib" in path.name:
            subprocess.run(["install_name_tool", "-id", f"@rpath/{path.name}", str(path)], check=True)

        for index, dependency in enumerate(dependencies):
            library_name = dependency.rsplit("/", 1)[-1]
            if library_name not in library_names:
                continue
            if index == 0 and path.parent == stage / "lib" and library_name == path.name:
                continue
            if is_system_macos_dependency(dependency):
                continue

            replacement = loader_path_reference(path, stage, library_name)
            if dependency != replacement:
                subprocess.run(
                    ["install_name_tool", "-change", dependency, replacement, str(path)],
                    check=True,
                )


def codesign_macos_binaries(stage: Path) -> None:
    if os.uname().sysname != "Darwin":
        return
    if shutil.which("codesign") is None:
        die("codesign is required to package patched macOS binaries")

    for path in macos_probe_paths(stage):
        if not otool_dependencies(path):
            continue
        subprocess.run(
            ["codesign", "--force", "--sign", "-", "--timestamp=none", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )


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
if [[ "{tool}" == "oshrun" ]]; then
  export OSHRUN_LAUNCHER="${{OSHRUN_LAUNCHER:-$prefix/bin/mpirun}}"
  export SHMEM_OFI_PROVIDER="${{SHMEM_OFI_PROVIDER:-sockets}}"
fi
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


def write_sos_compiler_launcher(stage: Path, tool: str) -> None:
    path = stage / "bin" / tool
    if path.exists() or path.is_symlink():
        real_dir = stage / "bin" / ".openmpi-real"
        real_dir.mkdir(parents=True, exist_ok=True)
        real_path = real_dir / tool
        if real_path.exists() or real_path.is_symlink():
            real_path.unlink()
        if path.is_symlink():
            real_path.symlink_to(os.readlink(path))
            path.unlink()
        else:
            shutil.move(str(path), real_path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)

    compiler_env = "SHMEM_CXX" if tool == "oshc++" else "SHMEM_CC"
    fallback_env = "CXX" if tool == "oshc++" else "CC"
    fallback_compiler = "c++" if tool == "oshc++" else "cc"
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
prefix="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
compiler="${{{compiler_env}:-${{{fallback_env}:-{fallback_compiler}}}}}"
compile_flags=(-I"$prefix/include")
link_flags=(-L"$prefix/lib" -Wl,-rpath,"$prefix/lib" -lsma -lpmix -levent_core -levent_pthreads -lhwloc -lfabric)
linking=1
for arg in "$@"; do
  case "$arg" in
    -c|-E|-S|-M|-MM)
      linking=0
      ;;
    --showme:compile|--showme:incdirs)
      printf '%s\\n' "${{compile_flags[*]}}"
      exit 0
      ;;
    --showme:link|--showme:libdirs|--showme:libs)
      printf '%s\\n' "${{link_flags[*]}}"
      exit 0
      ;;
    --showme)
      printf '%s %s\\n' "${{compile_flags[*]}}" "${{link_flags[*]}}"
      exit 0
      ;;
  esac
done
if [[ "$linking" == "1" ]]; then
  exec "$compiler" "${{compile_flags[@]}}" "$@" "${{link_flags[@]}}"
fi
exec "$compiler" "${{compile_flags[@]}}" "$@"
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
    sos_git_url: str | None,
    sos_ref: str | None,
    sos_commit: str | None,
    openshmem_provider: str,
    archive_name: str,
    sha256: str,
    sha256_scope: str,
    included_tools: list[str],
    bundled_runtime_libraries: list[str],
    mpi_smoke: str,
    shmem_smoke: str,
) -> dict[str, object]:
    openshmem_tools_present = "oshrun" in included_tools and bool(
        {"oshcc", "shmemcc"} & set(included_tools)
    )
    return {
        "package_name": package_name,
        "platform": platform,
        "os": package_os,
        "arch": arch,
        "openmpi_git_url": openmpi_git_url,
        "openmpi_ref": openmpi_ref,
        "openmpi_commit": openmpi_commit,
        "sos_git_url": sos_git_url,
        "sos_ref": sos_ref,
        "sos_commit": sos_commit,
        "openshmem_provider": openshmem_provider,
        "build_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "archive_name": archive_name,
        "sha256": sha256,
        "sha256_scope": sha256_scope,
        "capabilities": {
            "mpi": True,
            "openshmem": openshmem_tools_present and shmem_smoke == "passed",
        },
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
    sos_manifest_path = repo_root / "manifests" / "sos-version.json"
    sos_manifest = json.loads(sos_manifest_path.read_text(encoding="utf-8")) if sos_manifest_path.is_file() else {}
    sos_repo = repo_root / "external" / "sos"
    sos_git_url = git_output(sos_repo, "config", "--get", "remote.origin.url") or sos_manifest.get("sos_git_url")
    sos_ref = sos_manifest.get("sos_ref")
    sos_commit = git_output(sos_repo, "rev-parse", "HEAD") or sos_manifest.get("sos_commit")

    package_name = f"mpi-extensions-openmpi-{openmpi_ref}-{platform}"
    archive_name = f"{package_name}.tar.gz"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / archive_name

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        stage = tmpdir / package_name
        copy_install_tree(prefix, stage)
        sos_present = is_sos_install(stage)
        if sos_present and not (stage / "bin" / "shmemcc").exists():
            (stage / "bin" / "shmemcc").symlink_to("oshcc")

        licenses = stage / "LICENSES"
        licenses.mkdir(parents=True, exist_ok=True)
        shutil.copy2(openmpi_repo / "LICENSE", licenses / "OpenMPI-LICENSE.txt")
        shutil.copy2(repo_root / "LICENSE", licenses / "mpi-extensions-LICENSE.txt")
        if sos_present and (sos_repo / "LICENSE").is_file():
            shutil.copy2(sos_repo / "LICENSE", licenses / "Sandia-OpenSHMEM-LICENSE.txt")

        bundled_runtime_libraries = [
            *bundle_linux_runtime_dependencies(stage),
            *bundle_macos_runtime_dependencies(stage, prefix),
        ]
        if bundled_runtime_libraries:
            copy_optional_license(
                (
                    Path("/usr/share/doc/libucx0/copyright"),
                    Path("/usr/share/doc/libucx-dev/copyright"),
                    Path("/usr/share/doc/ucx/copyright"),
                ),
                licenses / "UCX-copyright.txt",
            )
        if any(name.startswith("libfabric") for name in bundled_runtime_libraries):
            copy_optional_license(
                (
                    Path("/opt/homebrew/opt/libfabric/COPYING"),
                    Path("/usr/local/opt/libfabric/COPYING"),
                ),
                licenses / "libfabric-COPYING.txt",
            )

        included_tools = detect_included_tools(stage)
        openshmem_provider = "none"
        if "oshrun" in included_tools and bool({"oshcc", "shmemcc"} & set(included_tools)):
            openshmem_provider = "sandia-openshmem-sos" if sos_present else "openmpi-oshmem"

        for tool in ("mpicc", "mpicxx", "mpirun", "mpiexec", "oshrun", "oshcc", "shmemcc", "oshc++"):
            if sos_present and tool in {"oshcc", "shmemcc"}:
                write_sos_compiler_launcher(stage, tool)
            elif sos_present and tool == "oshc++" and (stage / "bin" / tool).exists():
                write_sos_compiler_launcher(stage, tool)
            else:
                write_launcher(stage, tool)
        patch_macos_install_names(stage)
        codesign_macos_binaries(stage)

        internal_manifest_path = stage / "manifest.json"
        tree_sha = sha256_package_tree(stage, exclude={internal_manifest_path})
        internal_manifest = make_manifest(
            package_name=package_name,
            platform=platform,
            package_os=package_os,
            arch=arch,
            openmpi_git_url=openmpi_git_url,
            openmpi_ref=openmpi_ref,
            openmpi_commit=openmpi_commit,
            sos_git_url=sos_git_url,
            sos_ref=sos_ref,
            sos_commit=sos_commit,
            openshmem_provider=openshmem_provider,
            archive_name=archive_name,
            sha256=tree_sha,
            sha256_scope="package_tree_without_manifest",
            included_tools=included_tools,
            bundled_runtime_libraries=bundled_runtime_libraries,
            mpi_smoke=args.mpi_smoke,
            shmem_smoke=args.shmem_smoke,
        )
        write_json(internal_manifest_path, internal_manifest)

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
        sos_git_url=sos_git_url,
        sos_ref=sos_ref,
        sos_commit=sos_commit,
        openshmem_provider=openshmem_provider,
        archive_name=archive_name,
        sha256=archive_sha,
        sha256_scope="archive",
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
