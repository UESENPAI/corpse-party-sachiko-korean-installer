"""Validate the fixed public metadata-only Sachiko plan before game changes."""

import re

from sachiko_profile import PROFILE

ARCHIVES = {'scene.bcx', 'scene_jpn.bcx'}
POOL_SIZES = {entry['member']: entry['pool_count'] for entry in PROFILE['members']}
COPIES = {'scene.bcx': {'DATA.BIN', 'DBG00.BIN', 'MURDER.CFG', 'VOICE.CFG'},
          'scene_jpn.bcx': {'DATA.BIN'}}
HASH = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,96}\Z')
MEMBER_HASHES = {'source_sha256', 'input_archive_sha256', 'destination_sha256'}


def is_hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def validate_manifest(manifest):
    if (not isinstance(manifest, dict)
            or set(manifest) != {'schema', 'app_id', 'version', 'files', 'supported_sources'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or str(manifest['app_id']) != '778400'
            or not isinstance(manifest['version'], str) or not NAME.fullmatch(manifest['version'])):
        raise ValueError('Invalid public donor-only manifest')
    files = manifest['files']
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise ValueError('Public payload must contain one metadata plan')
    entry = files[0]
    if (set(entry) - {'path', 'sha256', 'source_sha256'} or entry.get('path') != 'recipe.json'
            or not is_hash(entry.get('sha256')) or entry.get('source_sha256', []) != []):
        raise ValueError('Public payload must contain only the verified recipe metadata')
    sources = manifest['supported_sources']
    if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
        raise ValueError('Expected one reviewed Steam source set')
    source = sources[0]
    if (set(source) != {'label', 'files'} or not isinstance(source['label'], str)
            or not NAME.fullmatch(source['label']) or not isinstance(source['files'], dict)
            or set(source['files']) != ARCHIVES | {'HystericBirthday.exe', 'font.bcx'}
            or not all(is_hash(value) for value in source['files'].values())):
        raise ValueError('Invalid original Steam fingerprints')
    return entry


def validate_plan(recipe, source):
    if (not isinstance(recipe, dict)
            or set(recipe) != {'format', 'app_id', 'executable_sha256', 'archives', 'font', 'psp_import'}
            or recipe['format'] != 'corpse-pc-bin-fields-v1' or str(recipe['app_id']) != '778400'
            or recipe['psp_import'] != 'sachiko-jdf'
            or recipe['executable_sha256'] != source['HystericBirthday.exe']):
        raise ValueError('Invalid Sachiko donor recipe')
    font = recipe['font']
    if (not isinstance(font, dict) or set(font) != {'source_sha256', 'ttf_sha256', 'codebook'}
            or font['source_sha256'] != source['font.bcx'] or not is_hash(font['ttf_sha256'])):
        raise ValueError('Invalid donor font profile')
    book = font['codebook']
    if (not isinstance(book, dict) or not 0 < len(book) <= 1880
            or any(len(char) != 1 or not '\uac00' <= char <= '\ud7a3'
                   or type(value) is not int or not 0xf0 <= value >> 8 <= 0xf9
                   or not (0x40 <= value & 0xff <= 0x7e or 0x80 <= value & 0xff <= 0xfc)
                   for char, value in book.items())
            or len(set(book.values())) != len(book)):
        raise ValueError('Invalid Korean glyph codebook')
    archives = recipe['archives']
    if not isinstance(archives, dict) or set(archives) != ARCHIVES:
        raise ValueError('Invalid target scene archives')
    for archive, section in archives.items():
        if (not isinstance(section, dict) or set(section) != {'source_sha256', 'members'}
                or section['source_sha256'] != source[archive] or not isinstance(section['members'], dict)
                or set(section['members']) != set(POOL_SIZES) | COPIES[archive]):
            raise ValueError(f'Invalid scene recipe for {archive}')
        for member, change in section['members'].items():
            field = 'copy_original' if member in COPIES[archive] else 'records'
            if (not isinstance(change, dict) or field not in change or 'source_sha256' not in change
                    or set(change) - MEMBER_HASHES - {'input_archive', 'allow_reflow', field}
                    or any(not is_hash(change[key]) for key in MEMBER_HASHES & change.keys())
                    or change.get('input_archive', archive) not in ARCHIVES
                    or type(change.get('allow_reflow', False)) is not bool):
                raise ValueError(f'Invalid donor member metadata for {member}')
            if field == 'copy_original':
                if change[field] is not True:
                    raise ValueError(f'Invalid original copy flag for {member}')
                continue
            records = change[field]
            if not isinstance(records, list) or not 0 < len(records) <= POOL_SIZES[member]:
                raise ValueError(f'Invalid donor record count for {member}')
            indices = set()
            for record in records:
                if not isinstance(record, dict) or set(record) != {'index', 'source_sha256', 'donor', 'target_sha256'}:
                    raise ValueError(f'Public recipe may contain only donor references for {member}')
                index = record['index']
                if (type(index) is not int or not 0 <= index < POOL_SIZES[member] or index in indices
                        or record['donor'] != [member, index] or not is_hash(record['source_sha256'])
                        or not is_hash(record['target_sha256'])):
                    raise ValueError(f'Invalid donor reference for {member}')
                indices.add(index)
