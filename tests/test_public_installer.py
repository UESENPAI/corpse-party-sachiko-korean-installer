"""Exercise metadata-only CLI and transactions against disposable Steam fixtures."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch as mock_patch

import installer
import patch
from project_config import ROOT
import psp_import
import runtime_payload


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    raw = json.dumps(value).encode('utf-8')
    path.write_bytes(raw)
    return digest(raw)


class PublicInstallerTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.game = self.root / 'steamapps/common/Test Game'
        self.game.mkdir(parents=True)
        (self.game.parent.parent / 'appmanifest_778400.acf').write_text(
            '"AppState" { "appid" "778400" "StateFlags" "4" "installdir" "Test Game" "buildid" "1" }',
            encoding='utf-8')
        self.originals = {name: f'fixture original: {name}'.encode('ascii')
                          for name in installer.MANAGED_FILES | {'HystericBirthday.exe'}}
        for name, raw in self.originals.items():
            (self.game / name).write_bytes(raw)
        self.slot = self.game / 'save_data/slot.dat'
        self.slot.parent.mkdir()
        self.slot.write_bytes(b'fixture user save')
        self.donor = self.root / 'fixture.7z'
        self.donor.write_bytes(b'synthetic donor bytes')
        self.source = self.root / 'payload'
        (self.source / 'files').mkdir(parents=True)
        self.manifest, self.recipe = installer.load_payload(ROOT / 'payload')
        fingerprints = self.manifest['supported_sources'][0]['files']
        fingerprints.update({name: digest(self.originals[name]) for name in fingerprints})
        self.recipe['executable_sha256'] = fingerprints['HystericBirthday.exe']
        self.recipe['font']['source_sha256'] = fingerprints['font.bcx']
        for name, section in self.recipe['archives'].items():
            section['source_sha256'] = fingerprints[name]
        self.write_payload()
        self.runtime = self.game / installer.STATE_DIR

    def write_payload(self):
        recipe_sha = write_json(self.source / 'files/recipe.json', self.recipe)
        self.manifest['files'][0]['sha256'] = recipe_sha
        write_json(self.source / 'manifest.json', self.manifest)

    def main(self, *args, code=0):
        output, error = io.StringIO(), io.StringIO()
        with mock_patch.object(patch, 'ROOT', self.root), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            actual = patch.main([*args, '--game-dir', str(self.game), '--json'])
        self.assertEqual(code, actual, error.getvalue())
        return json.loads((output if code == 0 else error).getvalue())

    def test_rejects_inline_translation_and_direct_payload(self):
        records = self.recipe['archives']['scene.bcx']['members']['CP2U00A.BIN']['records']
        records[0]['target'] = 'synthetic inline target'
        self.write_payload()
        with self.assertRaisesRegex(installer.PatchError, 'only donor references'):
            installer.load_payload(self.source)
        self.manifest['files'][0]['path'] = 'save_data/slot.dat'
        write_json(self.source / 'manifest.json', self.manifest)
        with self.assertRaisesRegex(installer.PatchError, 'only the verified recipe metadata'):
            installer.load_payload(self.source)

    def test_invalid_donor_leaves_state_and_slots_untouched(self):
        self.main('install', '--psp-patch', str(self.donor), code=1)
        self.assertFalse(self.runtime.exists())
        self.assertEqual(b'fixture user save', self.slot.read_bytes())

    def test_default_check_migration_and_donor_free_restore(self):
        self.runtime.mkdir()
        backups = self.runtime / 'originals'
        backups.mkdir()
        managed = {}
        for name in installer.MANAGED_FILES:
            original = self.originals[name]
            previous = f'fixture previous patch: {name}'.encode('ascii')
            (backups / digest(original)).write_bytes(original)
            (self.game / name).write_bytes(previous)
            managed[name] = {'installed_sha256': digest(previous), 'original_sha256': digest(original)}
        write_json(self.runtime / 'state.json', {'schema': 1, 'patch_id': installer.PATCH_ID,
                                                'app_id': '778400', 'version': 'fixture-prior', 'files': managed})
        fixture = self.donor.read_bytes()
        donor_profile = {'size': len(fixture), 'sha256': digest(fixture)}
        generated = {'scene.bcx', 'scene_jpn.bcx', 'font.bcx'}

        def materialize(game, recipe, archive, manifest, output):
            self.assertEqual(fixture, archive)
            files = output / 'files'
            files.mkdir(parents=True)
            entries = {}
            for name in generated:
                content = f'fixture current patch: {name}'.encode('ascii')
                (files / name).write_bytes(content)
                entries[name] = {'sha256': digest(content), 'source_sha256': [digest(self.originals[name])]}
            return output, manifest, entries

        with mock_patch.dict(psp_import.PROFILE, {'archive': donor_profile}), \
             mock_patch.object(runtime_payload, 'materialize', side_effect=materialize):
            checked = self.main('--psp-patch', str(self.donor))
            self.assertEqual('check', checked['action'])
            self.assertTrue(checked['dry_run'])
            self.assertFalse(checked['release_ready'])
            self.assertEqual('fixture-prior', self.main('status')['version'])
            self.assertEqual(5, self.main('install', '--psp-patch', str(self.donor))['changed_files'])

        for name in {'system.bcx', 'system_jpn.bcx'}:
            self.assertEqual(self.originals[name], (self.game / name).read_bytes())
        self.assertEqual(3, self.main('uninstall')['changed_files'])
        for name, raw in self.originals.items():
            self.assertEqual(raw, (self.game / name).read_bytes())
        self.assertEqual(b'fixture user save', self.slot.read_bytes())


if __name__ == '__main__':
    unittest.main()
