"""Isolated tests for the sanitized skill installer."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

import install_skill  # noqa: E402


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="install-test-", dir=PACKAGE_DIR)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source" / "copilot-consult"
        self.source.mkdir(parents=True)
        for relative in install_skill.ALLOWLIST:
            target = self.source.joinpath(*Path(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"synthetic test content: {relative}\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_dry_run_has_no_filesystem_effect(self):
        destination = self.root / "not-created" / "skills" / "copilot-consult"
        before = set(self.root.iterdir())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = install_skill.main(["--destination", str(destination)])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "DRY_RUN")
        self.assertFalse(payload["written"])
        self.assertFalse(destination.exists())
        self.assertFalse(destination.parent.parent.exists())
        self.assertEqual(set(self.root.iterdir()), before)

    def test_apply_copies_only_allowlist_and_emits_verifiable_manifest_sha(self):
        destination = self.root / "codex" / "skills" / "copilot-consult"
        plan = install_skill.prepare_install(self.source, destination)
        self.assertEqual(tuple(item.relative_path for item in plan.files), install_skill.ALLOWLIST)

        result = install_skill.apply_install(plan)
        self.assertEqual(result["status"], "INSTALLED")
        self.assertEqual(result["files"], 7)
        manifest_path = destination / install_skill.MANIFEST_NAME
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), result["manifest_sha256"])
        self.assertEqual(result["manifest_sha256"], plan.manifest_sha256)
        self.assertEqual([entry["path"] for entry in manifest["files"]], list(install_skill.ALLOWLIST))
        self.assertEqual(
            {entry.name for entry in destination.iterdir()},
            {"SKILL.md", "references", "scripts", install_skill.MANIFEST_NAME},
        )
        for entry in manifest["files"]:
            installed = destination.joinpath(*Path(entry["path"]).parts)
            self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), entry["sha256"])
            self.assertEqual(installed.stat().st_size, entry["size_bytes"])
        self.assertNotIn(str(self.source), manifest_bytes.decode("utf-8"))

    def test_existing_destination_is_never_overwritten(self):
        destination = self.root / "existing"
        plan = install_skill.prepare_install(self.source, destination)
        destination.mkdir()
        sentinel = destination / "keep.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(install_skill.InstallError, "already exists"):
            install_skill.prepare_install(self.source, destination)
        with self.assertRaisesRegex(install_skill.InstallError, "already exists"):
            install_skill.apply_install(plan)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertEqual([path.name for path in self.root.glob(".existing.stage-*")], [])

    def test_missing_source_and_allowlisted_file_are_rejected(self):
        with self.assertRaisesRegex(install_skill.InstallError, "source directory is missing"):
            install_skill.prepare_install(self.root / "missing", self.root / "destination")

        required = self.source / "scripts" / "protocol.py"
        required.unlink()
        with self.assertRaisesRegex(install_skill.InstallError, "Required allowlisted file is missing"):
            install_skill.prepare_install(self.source, self.root / "destination")

    def test_destination_inside_source_is_rejected(self):
        with self.assertRaisesRegex(install_skill.InstallError, "must not be inside"):
            install_skill.prepare_install(self.source, self.source / "nested" / "install")

    def test_symlinked_destination_is_rejected_when_platform_allows_links(self):
        target = self.root / "actual-destination"
        target.mkdir()
        link = self.root / "destination-link"
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("This Windows account does not permit creating symlinks")
        with self.assertRaisesRegex(install_skill.InstallError, "symlink or junction"):
            install_skill.prepare_install(self.source, link)

    def test_python_version_floor(self):
        with self.assertRaisesRegex(install_skill.InstallError, "Python 3.10 or newer"):
            install_skill.require_supported_python((3, 9, 9))
        install_skill.require_supported_python((3, 10, 0))

    def test_installer_help_is_safe(self):
        completed = subprocess.run(
            [sys.executable, str(PACKAGE_DIR / "install_skill.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--apply", completed.stdout)
        self.assertIn("--destination", completed.stdout)

    def test_installed_skill_cli_help_does_not_open_ui(self):
        destination = self.root / "smoke" / "skills" / "copilot-consult"
        install_skill.apply_install(
            install_skill.prepare_install(install_skill.bundled_skill_source(), destination)
        )
        cli = destination / "scripts" / "copilot.py"
        completed = subprocess.run(
            [sys.executable, str(cli), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())
        self.assertNotIn("UI_ACCESSIBLE", completed.stdout)


if __name__ == "__main__":
    unittest.main()
