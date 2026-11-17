"""Build an offline Go proxy without substituting the candidate module from cache."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

MAX_SUM_BYTES = 1 << 20
CACHE_PREREQUISITE = (
    "Go package checks use cached dependencies only. Run make check to populate the Go "
    "download cache before retrying; the candidate module always comes from the local archive."
)


def proxy_path(module: str) -> str:
    return "".join(
        "!" + character.lower() if character.isupper() else character for character in module
    )


def overlay_dependencies(source: Path, destination: Path, candidate: tuple[str, ...]) -> None:
    """Link cached module trees, excluding the entire candidate module subtree."""
    if not source.is_dir():
        return
    for path in source.iterdir():
        if path.name == candidate[0]:
            if len(candidate) > 1 and path.is_dir():
                child = destination / path.name
                child.mkdir(exist_ok=True)
                overlay_dependencies(path, child, candidate[1:])
        elif path.name != "sumdb":
            (destination / path.name).symlink_to(path, target_is_directory=path.is_dir())


def prepare_consumer(
    artifacts: Path,
    consumer: Path,
    proxy: Path,
    modcache: Path,
    *,
    module: str,
    version: str,
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Use packaged checksums and a new consumer/cache; the network is disabled."""
    environment = dict(os.environ if environment is None else environment)
    cached = subprocess.run(
        ["go", "env", "GOMODCACHE"],
        cwd=consumer,
        env={**environment, "GOTOOLCHAIN": "local", "GOFLAGS": "", "GOWORK": "off"},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if not cached or not Path(cached).is_absolute():
        raise ValueError("go env GOMODCACHE did not return an absolute cache path")
    proxy.mkdir(parents=True, exist_ok=False)
    candidate = tuple(proxy_path(module).split("/"))
    overlay_dependencies(Path(cached) / "cache/download", proxy, candidate)
    target = proxy.joinpath(*candidate, "@v")
    target.mkdir(parents=True)
    for extension in ("zip", "mod", "info"):
        shutil.copyfile(artifacts / f"{version}.{extension}", target / f"{version}.{extension}")
    (target / "list").write_text(version + "\n")
    with zipfile.ZipFile(target / f"{version}.zip") as archive:
        try:
            checksum = archive.getinfo(f"{module}@{version}/go.sum")
        except KeyError:
            pass
        else:
            if checksum.file_size > MAX_SUM_BYTES:
                raise ValueError("Packaged go.sum exceeds the checksum size limit")
            (consumer / "go.sum").write_bytes(archive.read(checksum))
    (consumer / "go.mod").write_text(
        f"module anchorpass-package-check\n\ngo 1.23.0\n\nrequire {module} {version}\n"
    )
    return {
        **environment,
        "GOPROXY": proxy.as_uri(),
        "GOSUMDB": "off",
        "GOMODCACHE": str(modcache),
        "GOWORK": "off",
        "GOTOOLCHAIN": "local",
        "GOENV": "off",
        "GOFLAGS": "",
        "GOPRIVATE": "",
        "GONOPROXY": "none",
        "GONOSUMDB": "none",
        "GOVCS": "*:off",
    }
