"""Install the sanitized Copilot consultation skill into a Codex skills folder.

The default invocation is a read-only dry run. Copying requires ``--apply``.
Only the fixed source allowlist is copied; this installer never runs skill code.
Updates require --update, verify the prior manifest and preserve a backup.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from typing import Iterable


SKILL_NAME = "copilot-consult"
MANIFEST_NAME = "install-manifest.json"
ALLOWLIST = (
    "SKILL.md",
    "references/operation.md",
    "scripts/copilot.py",
    "scripts/app.py",
    "scripts/native_uia.py",
    "scripts/protocol.py",
    "scripts/response_text.py",
    "scripts/authorization.py",
    "scripts/hook_dispatch.py",
    "scripts/hook_admin.py",
    "references/hooks.md",
)
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class InstallError(RuntimeError):
    """The requested installation is unsafe or incomplete."""


@dataclass(frozen=True)
class PlannedFile:
    relative_path: str
    contents: bytes
    sha256: str


@dataclass(frozen=True)
class InstallPlan:
    source: Path
    destination: Path
    files: tuple[PlannedFile, ...]
    manifest_bytes: bytes
    manifest_sha256: str


def require_supported_python(version: Iterable[int] = sys.version_info) -> None:
    parts = tuple(version)
    if len(parts) < 2 or tuple(parts[:2]) < (3, 10):
        raise InstallError("Python 3.10 or newer is required")


def bundled_skill_source() -> Path:
    """Return the skill source bundled beside this installer."""
    return Path(__file__).resolve().parent / "skill" / SKILL_NAME


def default_destination() -> Path:
    """Resolve CODEX_HOME or the user's local ~/.codex skills directory."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    codex_home = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return codex_home / "skills" / SKILL_NAME


def _absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        details = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InstallError(f"Cannot safely inspect path component: {path}") from exc
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _reject_link_components(path: Path, label: str) -> None:
    absolute = _absolute(path)
    components = (absolute, *absolute.parents)
    for component in components:
        if _lexists(component) and _is_link_or_junction(component):
            raise InstallError(f"{label} contains a symlink or junction: {component}")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(path), os.fspath(parent)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(parent))


def _validate_source(source: str | os.PathLike[str]) -> tuple[Path, tuple[PlannedFile, ...]]:
    source_path = _absolute(source)
    _reject_link_components(source_path, "Source")
    if not source_path.is_dir():
        raise InstallError(f"Skill source directory is missing: {source_path}")
    source_path = source_path.resolve(strict=True)

    planned: list[PlannedFile] = []
    for relative in ALLOWLIST:
        source_file = source_path.joinpath(*PurePosixPath(relative).parts)
        _reject_link_components(source_file, "Source")
        if not source_file.is_file():
            raise InstallError(f"Required allowlisted file is missing: {relative}")
        try:
            contents = source_file.read_bytes()
        except OSError as exc:
            raise InstallError(f"Could not read allowlisted file: {relative}") from exc
        planned.append(
            PlannedFile(relative, contents, hashlib.sha256(contents).hexdigest())
        )
    return source_path, tuple(planned)


def _manifest_bytes(files: tuple[PlannedFile, ...]) -> bytes:
    manifest_data = {
        "format_version": 1,
        "skill": SKILL_NAME,
        "files": [
            {"path": item.relative_path, "sha256": item.sha256, "size_bytes": len(item.contents)}
            for item in files
        ],
    }
    return (json.dumps(manifest_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def prepare_install(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> InstallPlan:
    """Validate paths and hash the allowlist without writing any files."""
    require_supported_python()
    source_path, files = _validate_source(source)
    destination_path = _absolute(destination)
    _reject_link_components(destination_path, "Destination")
    destination_resolved = destination_path.resolve(strict=False)
    if _is_within(destination_resolved, source_path):
        raise InstallError("Destination must not be inside the bundled skill source")
    if _lexists(destination_path):
        raise InstallError(f"Destination already exists; refusing to overwrite: {destination_path}")

    manifest_bytes = _manifest_bytes(files)
    return InstallPlan(
        source_path,
        destination_path,
        files,
        manifest_bytes,
        hashlib.sha256(manifest_bytes).hexdigest(),
    )


def apply_install(plan: InstallPlan) -> dict[str, object]:
    """Write a sibling staging directory, verify it, then rename atomically."""
    require_supported_python()
    if tuple(item.relative_path for item in plan.files) != ALLOWLIST:
        raise InstallError("Install plan does not match the fixed source allowlist")
    for item in plan.files:
        if hashlib.sha256(item.contents).hexdigest() != item.sha256:
            raise InstallError(f"Install plan SHA-256 mismatch: {item.relative_path}")
    expected_manifest = _manifest_bytes(plan.files)
    if plan.manifest_bytes != expected_manifest:
        raise InstallError("Install plan manifest does not match the allowlisted files")
    if hashlib.sha256(expected_manifest).hexdigest() != plan.manifest_sha256:
        raise InstallError("Install plan manifest SHA-256 is invalid")

    _reject_link_components(plan.source, "Source")
    destination = plan.destination
    _reject_link_components(destination, "Destination")
    resolved_source = plan.source.resolve(strict=True)
    resolved_destination = destination.resolve(strict=False)
    if _is_within(resolved_destination, resolved_source):
        raise InstallError("Destination must not be inside the bundled skill source")
    if _lexists(destination):
        raise InstallError(f"Destination already exists; refusing to overwrite: {destination}")

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallError(f"Could not create destination parent: {destination.parent}") from exc
    _reject_link_components(destination.parent, "Destination parent")

    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=destination.parent))
        _reject_link_components(stage, "Staging directory")
        for item in plan.files:
            target = stage.joinpath(*PurePosixPath(item.relative_path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            _reject_link_components(target.parent, "Staging directory")
            with target.open("xb") as stream:
                stream.write(item.contents)
            if hashlib.sha256(target.read_bytes()).hexdigest() != item.sha256:
                raise InstallError(f"Staged file failed SHA-256 verification: {item.relative_path}")

        manifest_path = stage / MANIFEST_NAME
        with manifest_path.open("xb") as stream:
            stream.write(plan.manifest_bytes)
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != plan.manifest_sha256:
            raise InstallError("Staged install manifest failed SHA-256 verification")

        # On Windows os.rename fails if the destination appeared concurrently;
        # the same-volume sibling stage keeps the successful rename atomic.
        _reject_link_components(destination.parent, "Destination parent")
        if _lexists(destination):
            raise InstallError(f"Destination appeared during installation; refusing to overwrite: {destination}")
        os.rename(stage, destination)
    except Exception as exc:
        if isinstance(exc, InstallError):
            message = str(exc)
        else:
            message = f"Installation failed: {exc}"
        if stage is not None and _lexists(stage):
            message += f"; staged files were preserved at {stage}"
        raise InstallError(message) from exc

    return {
        "status": "INSTALLED",
        "destination": os.fspath(destination),
        "files": len(plan.files),
        "manifest_sha256": plan.manifest_sha256,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true", help="Update only manifest-managed files after verifying them; backup required and preserved",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        help="Install path (default: CODEX_HOME/skills/copilot-consult or ~/.codex/skills/copilot-consult)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy the allowlisted skill into a new destination (default is a no-write dry run)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        require_supported_python()
        destination = args.destination or default_destination()
        if args.update:
            result=update_install(bundled_skill_source(),destination,apply=args.apply)
            print(json.dumps(result,ensure_ascii=True,indent=2))
            return 0
        plan = prepare_install(bundled_skill_source(), destination)
        if args.apply:
            result = apply_install(plan)
        else:
            result = {
                "status": "DRY_RUN",
                "destination": os.fspath(plan.destination),
                "files": len(plan.files),
                "manifest_sha256": plan.manifest_sha256,
                "written": False,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except InstallError as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 2


def _atomic_file(path,data):
    _reject_link_components(path,"Managed update file")
    path.parent.mkdir(parents=True,exist_ok=True)
    _reject_link_components(path.parent,"Managed update directory")
    descriptor,name=tempfile.mkstemp(prefix=".copilot-update-",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(descriptor,"wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def update_install(source,destination,*,apply=False):
    source_path,files=_validate_source(source)
    destination=_absolute(destination)
    _reject_link_components(destination,"Installed skill")
    if _is_within(destination.resolve(),source_path):
        raise InstallError("Update destination cannot be inside the source")
    manifest_path=destination/MANIFEST_NAME
    try:
        old_manifest=manifest_path.read_bytes()
        entries=json.loads(old_manifest)["files"]
        if not isinstance(entries,list) or len({item["path"] for item in entries})!=len(entries):
            raise InstallError("Invalid prior manifest")
        old={}
        for entry in entries:
            if entry["path"] not in ALLOWLIST:
                raise InstallError("Prior manifest contains an unmanaged path")
            path=destination/entry["path"]
            _reject_link_components(path,"Installed file")
            contents=path.read_bytes()
            if hashlib.sha256(contents).hexdigest()!=entry["sha256"]:
                raise InstallError("Installed file was modified; preserve it: "+entry["path"])
            old[entry["path"]]=contents
        for item in files:
            if item.relative_path not in old and _lexists(destination/item.relative_path):
                raise InstallError("New managed path already contains an unrelated file: "+item.relative_path)
    except (KeyError,TypeError,ValueError,OSError) as exc:
        raise InstallError("Cannot verify existing installation manifest") from exc
    manifest=_manifest_bytes(files)
    summary={"status":"DRY_RUN_UPDATE","files":len(files),"manifest_sha256":hashlib.sha256(manifest).hexdigest(),"written":False}
    if not apply:
        return summary
    backup_root=destination.parent.parent/".copilot-consult-backups"
    _reject_link_components(backup_root,"Backup")
    backup_root.mkdir(parents=True,exist_ok=True)
    backup=Path(tempfile.mkdtemp(prefix="update-",dir=backup_root))
    for relative,data in {**old,MANIFEST_NAME:old_manifest}.items():
        target=backup/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    changed=[]
    try:
        if manifest_path.read_bytes()!=old_manifest or any((destination/name).read_bytes()!=data for name,data in old.items()):
            raise InstallError("Installation changed during update review")
        for item in files:
            _atomic_file(destination/item.relative_path,item.contents)
            changed.append(item)
        _atomic_file(manifest_path,manifest)
        for item in files:
            if (destination/item.relative_path).read_bytes()!=item.contents:
                raise InstallError("Installed update failed byte verification")
    except Exception as exc:
        for item in reversed(changed):
            target=destination/item.relative_path
            if target.read_bytes()!=item.contents:
                continue  # Never overwrite an intervening user change.
            if item.relative_path in old:
                _atomic_file(target,old[item.relative_path])
            else:
                target.unlink()
        if manifest_path.read_bytes()==manifest:
            _atomic_file(manifest_path,old_manifest)
        raise InstallError("Managed update failed; backup preserved at "+str(backup)) from exc
    return {**summary,"status":"UPDATED","written":True,"backup":str(backup),"hooks_registered":False,"trust_granted":False}


if __name__ == "__main__":
    raise SystemExit(main())
