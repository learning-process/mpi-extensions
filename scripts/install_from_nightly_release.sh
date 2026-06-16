#!/usr/bin/env python3
"""Install the current nightly Open MPI package from GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    import requests
except ImportError as exc:  # pragma: no cover - depends on caller environment
    if os.environ.get("MPI_EXTENSIONS_INSTALLER_BOOTSTRAPPED") == "1":
        print(
            "error: missing Python dependency 'requests' after bootstrap; "
            "run 'python3 -m pip install -r requirements.txt'",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    script_path = Path(__file__).resolve()
    repo_root_for_bootstrap = script_path.parents[1]
    requirements = repo_root_for_bootstrap / "requirements.txt"

    venv_dir = Path(
        os.environ.get(
            "MPI_EXTENSIONS_INSTALLER_VENV",
            Path.home() / ".cache" / "mpi-extensions" / "installer-venv",
        )
    ).expanduser()
    print(f"Bootstrapping Python dependencies in {venv_dir}", file=sys.stderr)
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python_bin = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    pip_bin = venv_dir / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    install_args = [str(pip_bin), "install"]
    if requirements.is_file():
        install_args.extend(["-r", str(requirements)])
    else:
        install_args.append("requests>=2.32.0,<3")
    subprocess.run(install_args, check=True)

    env = os.environ.copy()
    env["MPI_EXTENSIONS_INSTALLER_BOOTSTRAPPED"] = "1"
    os.execve(str(python_bin), [str(python_bin), str(script_path), *sys.argv[1:]], env)


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


def github_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mpi-extensions-nightly-installer",
        }
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def request_json(session: requests.Session, url: str) -> dict[str, object]:
    response = session.get(url, timeout=60)
    if response.status_code == 404:
        die(f"nightly pre-release not found at {url}")
    response.raise_for_status()
    return response.json()


def download(session: requests.Session, url: str, path: Path) -> None:
    with session.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def select_assets(release: dict[str, object], platform: str) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    if release.get("tag_name") != "nightly":
        die("release tag is not nightly")
    if not release.get("prerelease", False):
        die("nightly release is not marked as a prerelease")

    assets = release.get("assets")
    if not isinstance(assets, list):
        die("nightly release metadata does not contain assets")

    archive = None
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.startswith("mpi-extensions-openmpi-") and name.endswith(f"-{platform}.tar.gz"):
            archive = asset
            break
    if archive is None:
        die(f"no nightly archive asset found for platform {platform}")

    by_name = {str(asset.get("name", "")): asset for asset in assets}
    checksum = by_name.get(f"{archive['name']}.sha256")
    manifest = by_name.get(f"{archive['name']}.manifest.json")
    if checksum is None:
        die(f"checksum asset missing: {archive['name']}.sha256")
    if manifest is None:
        die(f"manifest asset missing: {archive['name']}.manifest.json")
    return archive, checksum, manifest


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not str(target).startswith(str(destination) + os.sep):
                die(f"unsafe path in archive: {member.name}")
        tar.extractall(destination)


def copy_clean(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="learning-process/mpi-extensions", help="GitHub repository owner/name")
    parser.add_argument(
        "--platform",
        default="auto",
        choices=("auto", "linux-x86_64", "linux-arm64", "macos-arm64", "macos-x86_64"),
        help="Package platform",
    )
    parser.add_argument("--prefix", required=True, help="Install directory")
    args = parser.parse_args()

    platform = detect_platform() if args.platform == "auto" else args.platform
    prefix = Path(args.prefix).expanduser().resolve()
    session = github_session()
    release_url = f"https://api.github.com/repos/{args.repo}/releases/tags/nightly"
    release = request_json(session, release_url)
    archive_asset, checksum_asset, manifest_asset = select_assets(release, platform)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        archive_path = tmpdir / str(archive_asset["name"])
        checksum_path = tmpdir / str(checksum_asset["name"])
        manifest_path = tmpdir / str(manifest_asset["name"])

        download(session, str(archive_asset["browser_download_url"]), archive_path)
        download(session, str(checksum_asset["browser_download_url"]), checksum_path)
        download(session, str(manifest_asset["browser_download_url"]), manifest_path)

        expected_sha = checksum_path.read_text(encoding="utf-8").split()[0]
        actual_sha = sha256_file(archive_path)
        if expected_sha != actual_sha:
            die(f"SHA256 mismatch for {archive_path.name}: expected {expected_sha}, got {actual_sha}")

        extract_dir = tmpdir / "extract"
        extract_dir.mkdir()
        safe_extract_tar(archive_path, extract_dir)
        roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            die("archive must contain exactly one top-level directory")
        copy_clean(roots[0], prefix)

    print(f"MPI_HOME={prefix}")
    print(f"OPAL_PREFIX={prefix}")
    print(f"PATH={prefix / 'bin'}:$PATH")
    print(f"LD_LIBRARY_PATH={prefix / 'lib'}:${{LD_LIBRARY_PATH:-}}")
    print(f"DYLD_LIBRARY_PATH={prefix / 'lib'}:${{DYLD_LIBRARY_PATH:-}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
