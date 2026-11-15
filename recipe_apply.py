"""Rebuild verified Sachiko scene and font resources in temporary storage."""
import hashlib
from pathlib import Path

import converter_dxa as dxa
import pc_bin
import pc_scene

FORMAT = 'corpse-pc-bin-fields-v1'
ARCHIVES = {'scene.bcx', 'scene_jpn.bcx'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def preserve_speaker_key(source, target, keys):
    """Preserve the engine's leading lookup key, not its visible display name."""
    if not source.startswith(b'\x81\x79'):
        return target, b''
    key = next((key for key in keys if source.startswith(key)), b'')
    if not key:
        return target, b''
    target_end = target.find('】')
    if not target.startswith('【') or target_end < 0:
        raise ValueError('Localized dialogue moved its leading speaker key')
    return key.decode('cp932') + target[target_end + 1:], key


def prepare(game, recipe, archive, output):
    from project_config import GAME, ROOT
    from installer import checked_path, digest, original_file, SHA256
    if (recipe.get('format') != FORMAT or str(recipe.get('app_id')) != str(GAME['app_id'])
            or str(GAME['app_id']) != '778400'):
        raise ValueError('Unsupported PC translation recipe')
    archives = recipe.get('archives')
    if not isinstance(archives, dict) or not archives or set(archives) - ARCHIVES:
        raise ValueError('Unexpected PC translation archives')
    output = Path(output)
    if output.exists():
        raise ValueError('Rebuilt resources require a new temporary directory')
    output.mkdir(parents=True)
    book = recipe['font']['codebook']
    if not isinstance(book, dict) or not book or len(book) > 1880:
        raise ValueError('Invalid Korean glyph codebook')
    expected_exe = recipe.get('executable_sha256', '')
    original_file(game, GAME['key_file'], expected_exe)
    import psp_import
    donors = psp_import.recover(archive)
    generated, context, archive_cache = [], {}, {}

    def archive_input(name, expected):
        if name not in ARCHIVES:
            raise ValueError('Unexpected input archive')
        key = (name, expected)
        if key not in archive_cache:
            path = original_file(game, name, expected, context)
            archive_cache[key] = (path, {e['path']: e for e in dxa.inspect(path)['files']})
        return archive_cache[key]

    for name, section in sorted(archives.items()):
        source, entries = archive_input(name, section['source_sha256'])
        replacements = {}
        speaker_keys = ()
        # DATA.BIN defines the exact leading tags that the scene engine strips
        # before displaying dialogue. Process that table before scene scripts.
        members = sorted(section['members'].items(), key=lambda item: item[0] != 'DATA.BIN')
        for member, change in members:
            is_copy = change.get('copy_original') is True
            if member not in entries:
                raise ValueError('Unexpected source script member')
            input_name = change.get('input_archive', name)
            input_hash = change.get('input_archive_sha256', section['source_sha256'])
            input_path, input_entries = archive_input(input_name, input_hash)
            if member not in input_entries:
                raise ValueError('Japanese source member is missing')
            if sha(dxa.read_member(source, entries[member])) != change.get('destination_sha256', change['source_sha256']):
                raise ValueError('Original destination member fingerprint mismatch')
            raw = dxa.read_member(input_path, input_entries[member])
            if sha(raw) != change['source_sha256']:
                raise ValueError('Original script fingerprint mismatch')
            if member == 'DATA.BIN':
                data_script = pc_bin.parse(raw)
                indices = pc_bin.data_speaker_key_indices(raw, GAME['app_id'])
                speaker_keys = tuple(data_script.strings[index] for index in indices)
            if is_copy:
                replacements[member] = raw
                continue
            strings = pc_bin.parse(raw).strings
            edits = {}
            for record in change['records']:
                index = record['index']
                if not 0 <= index < len(strings) or index in edits:
                    raise ValueError('Invalid or duplicate localized string index')
                text = strings[index].decode('cp932')
                if sha(text.encode('utf-8')) != record['source_sha256']:
                    raise ValueError('Original text fingerprint mismatch')
                target = psp_import.target(record, donors)
                target, prefix = preserve_speaker_key(strings[index], target, speaker_keys)
                edits[index] = {'source': text, 'target': target}
                if prefix:
                    edits[index]['verbatim_prefix'] = prefix
            updated = pc_bin.replace(raw, edits, book, allow_reflow=change.get('allow_reflow', False))
            replacements[member] = pc_scene.relocate(raw, updated, member, GAME['app_id'])
        if not replacements:
            raise ValueError('Empty archive localization recipe')
        target = output / name
        result = dxa.rebuild(source, replacements, target)
        generated.append({'path': name, 'sha256': result['sha256'], 'source_sha256': [section['source_sha256']]})
    font = recipe['font']
    source = original_file(game, 'font.bcx', font['source_sha256'], context)
    asset = checked_path(ROOT, 'assets/NotoSansKR.ttf')
    if not SHA256.fullmatch(font.get('ttf_sha256', '')) or digest(asset) != font['ttf_sha256']:
        raise ValueError('Bundled Korean font fingerprint mismatch')
    from korean_font import build
    report = build(source, book, output / 'font.bcx', asset)
    if report['verified_glyphs'] != len(book) or not report['original_glyphs_and_bitmap_bytes_preserved']:
        raise ValueError('Korean font resource verification failed')
    generated.append({'path': 'font.bcx', 'sha256': report['resource_sha256'], 'source_sha256': [font['source_sha256']]})
    return generated
