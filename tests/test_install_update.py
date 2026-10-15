"""Managed upgrade tests with synthetic sources and isolated destinations."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

PACKAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PACKAGE))
import install_skill


class ManagedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="managed-update-",dir=PACKAGE)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/"source"
        for relative in install_skill.ALLOWLIST:
            file=self.source/relative
            file.parent.mkdir(parents=True,exist_ok=True)
            file.write_text("synthetic source: "+relative,encoding="utf-8")
        self.destination=self.root/"codex"/"skills"/"copilot-consult"
        plan=install_skill.prepare_install(self.source,self.destination)
        install_skill.apply_install(plan)
        # Model an older seven-file installation, keeping its verified manifest.
        legacy=plan.files[:7]
        for entry in plan.files[7:]:
            (self.destination/entry.relative_path).unlink()
        (self.destination/install_skill.MANIFEST_NAME).write_bytes(install_skill._manifest_bytes(legacy))
        (self.source/"SKILL.md").write_text("updated synthetic instructions",encoding="utf-8")

    def test_update_dryrun_writes_nothing(self):
        before=(self.destination/"SKILL.md").read_bytes()
        result=install_skill.update_install(self.source,self.destination)
        self.assertFalse(result["written"])
        self.assertEqual((self.destination/"SKILL.md").read_bytes(),before)
        self.assertFalse((self.destination.parent.parent/".copilot-consult-backups").exists())

    def test_upgrade_legacy_preserves_unmanaged_file_and_backup(self):
        sentinel=self.destination/"keep-local.txt"
        sentinel.write_text("synthetic user notes",encoding="utf-8")
        old=(self.destination/"SKILL.md").read_bytes()
        result=install_skill.update_install(self.source,self.destination,apply=True)
        self.assertEqual(result["status"],"UPDATED")
        self.assertFalse(result["hooks_registered"])
        self.assertFalse(result["trust_granted"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"),"synthetic user notes")
        self.assertEqual((Path(result["backup"])/"SKILL.md").read_bytes(),old)
        manifest=json.loads((self.destination/install_skill.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["files"]),11)
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256((self.destination/item["path"]).read_bytes()).hexdigest(),item["sha256"])

    def test_modified_managed_file_is_preserved(self):
        skill=self.destination/"SKILL.md"
        skill.write_text("synthetic local modification",encoding="utf-8")
        with self.assertRaises(install_skill.InstallError):
            install_skill.update_install(self.source,self.destination,apply=True)
        self.assertEqual(skill.read_text(encoding="utf-8"),"synthetic local modification")

    def test_new_managed_path_does_not_replace_unrelated_file(self):
        target=self.destination/"scripts"/"hook_dispatch.py"
        target.write_text("synthetic unrelated file",encoding="utf-8")
        with self.assertRaises(install_skill.InstallError):
            install_skill.update_install(self.source,self.destination,apply=True)
        self.assertEqual(target.read_text(encoding="utf-8"),"synthetic unrelated file")


if __name__=="__main__":
    unittest.main()
