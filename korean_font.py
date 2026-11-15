"""Local, source-only Korean DFT generation from an external TTF.

All original character records and bitmap bytes survive. The DX4 archive is
rebuilt compactly using its own key, with no copy of obsolete archive data.
The TTF is registered only as a private memory resource, never system-wide.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import time

import converter_dxa as dxa
import dft_font as dft
from windows_font import PrivateFont as WindowsPrivateFont
from freetype_font import PrivateFont as FreeTypePrivateFont

TABLES = tuple(bytes(value ^ key for value in range(256)) for key in range(256))


def renderer_for_platform(platform=None):
    platform = os.name if platform is None else platform
    if platform == 'nt':
        return WindowsPrivateFont
    if platform == 'posix':
        return FreeTypePrivateFont
    raise ValueError('No supported font renderer for this operating system')


def crypt(data, key, offset):
    output = bytearray(len(data))
    for index in range(len(key)):
        output[index::len(key)] = data[index::len(key)].translate(TABLES[key[(offset + index) % len(key)]])
    return bytes(output)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _slots(codebook):
    if not isinstance(codebook, dict) or not 0 < len(codebook) <= 1880:
        raise ValueError('Expected a nonempty CP932 Korean codebook')
    result = {}
    for character, code in codebook.items():
        if (not isinstance(character, str) or len(character) != 1 or ord(character) > 0xffff
                or not isinstance(code, int) or isinstance(code, bool) or not 0xf040 <= code <= 0xf9fc):
            raise ValueError('Invalid Korean character or CP932 slot')
        try:
            stored = code.to_bytes(2, 'big').decode('cp932', 'strict')
        except UnicodeError as exc:
            raise ValueError('Invalid CP932 user-defined slot') from exc
        if len(stored) != 1 or not 0xe000 <= ord(stored) <= 0xe757 or ord(stored) in result:
            raise ValueError('Duplicate or invalid PUA slot')
        result[ord(stored)] = character
    return result


def _read_member(source, entry, key):
    source.seek(entry['offset'])
    raw = source.read(entry['stored_size'])
    if len(raw) != entry['stored_size']:
        raise ValueError('Truncated original font member')
    data = crypt(raw, key, entry['offset'])
    return dxa.decompress(data, entry['size']) if entry['compressed'] else data


def _order(entry):
    match = re.search(r'(\d+)\.DFT$', entry['path'], re.IGNORECASE)
    return (int(match[1]) if match else 0, entry['path'])


def _rebuild(source, codebook, output, rasterizer, *, expected_members=None, progress=None):
    """Backend-independent streaming implementation, also used by synthetic tests."""
    source, output = Path(source), Path(output)
    if (source.is_symlink() or not source.is_file() or output.exists() or output.is_symlink()
            or source.resolve() == output.resolve()):
        raise ValueError('Font input must be original; output must be a new file')
    slots = _slots(codebook)
    entries = dxa.inspect(source)['files']
    if not entries or len({entry['path'] for entry in entries}) != len(entries):
        raise ValueError('Invalid font archive directory')
    if expected_members is not None and {entry['path'] for entry in entries} != set(expected_members):
        raise ValueError('Expected all 256 original font resources')
    if any(not entry['path'].upper().endswith('.DFT') for entry in entries):
        raise ValueError('Unexpected non-font archive member')
    started = time.monotonic()
    original_stat = source.stat()
    original_sha = file_sha256(source)
    reports, by_name = [], {}
    cache_size, masks = None, {}

    def raster(character, size):
        nonlocal cache_size, masks
        if size != cache_size:
            cache_size, masks = size, {}
        if character not in masks:
            masks[character] = rasterizer.raster(character, size)
        return masks[character]

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open('rb') as original:
            encrypted_header = original.read(28)
            matches = [key for key in dxa.KEYS if crypt(encrypted_header[:2], key, 0) == b'DX']
            if len(matches) != 1:
                raise ValueError('Unknown original DX font archive key')
            key = matches[0]
            header = bytearray(crypt(encrypted_header, key, 0))
            _, version, _, base, info, _, _, _ = struct.unpack('<2sH6I', header)
            if version != 4 or base != 28 or not 28 <= info < original_stat.st_size:
                raise ValueError('Unsupported DX font archive layout')
            original.seek(info)
            metadata = bytearray(crypt(original.read(), key, info))
            with output.open('xb') as target:
                target.write(encrypted_header)
                # Group equal font sizes to reuse GDI masks for the two families.
                for index, entry in enumerate(sorted(entries, key=_order)):
                    raw = _read_member(original, entry, key)
                    changed, old, new = dft.append_native(raw, slots, raster)
                    report = {'member': entry['path'], 'size': old['size'], 'old_count': old['count'],
                              'new_count': new['count'], 'old_image_bytes': len(old['images']),
                              'original_records_sha256': _sha(dft.record_bytes(old['records'])),
                              'original_images_sha256': _sha(old['images']), 'press_sha256': _sha(old['press']),
                              'appended_images_sha256': _sha(new['images'][len(old['images']):]),
                              'resource_sha256': _sha(changed), 'resource_bytes': len(changed)}
                    cursor = target.tell()
                    if cursor % 4:
                        target.write(crypt(bytes(4 - cursor % 4), key, cursor))
                        cursor = target.tell()
                    target.write(crypt(changed, key, cursor))
                    struct.pack_into('<III', metadata, entry['entry_offset'] - info + 32,
                                     cursor - base, len(changed), 0xffffffff)
                    reports.append(report)
                    by_name[entry['path']] = report
                    if progress:
                        progress({'fonts': index + 1, 'total': len(entries), 'bytes': target.tell(),
                                  'seconds': round(time.monotonic() - started, 2)})
                cursor = target.tell()
                if cursor % 4:
                    target.write(crypt(bytes(4 - cursor % 4), key, cursor))
                new_info = target.tell()
                if new_info + len(metadata) >= 2 ** 32:
                    raise ValueError('Rebuilt font archive exceeds the DX4 address space')
                target.write(crypt(metadata, key, new_info))
                struct.pack_into('<I', header, 12, new_info)
                target.seek(0)
                target.write(crypt(header, key, 0))
        rebuilt = dxa.inspect(output)['files']
        if {entry['path'] for entry in rebuilt} != set(by_name):
            raise ValueError('Rebuilt font archive directory changed')
        with output.open('rb') as archive:
            if crypt(archive.read(2), key, 0) != b'DX':
                raise ValueError('Original font archive key was not preserved')
            for entry in rebuilt:
                raw = _read_member(archive, entry, key)
                report = by_name[entry['path']]
                if _sha(raw) != report['resource_sha256']:
                    raise ValueError('Re-extracted font resource hash mismatch')
                parsed = dft.parse(raw)
                old_count, image_bytes = report['old_count'], report['old_image_bytes']
                added = parsed['records'][old_count:]
                if (parsed['count'] != old_count + len(slots) or [record[0] for record in added] != sorted(slots)
                        or _sha(dft.record_bytes(parsed['records'][:old_count])) != report['original_records_sha256']
                        or _sha(parsed['images'][:image_bytes]) != report['original_images_sha256']
                        or _sha(parsed['press']) != report['press_sha256']
                        or _sha(parsed['images'][image_bytes:]) != report['appended_images_sha256']):
                    raise ValueError('Re-extracted original/glyph-table preservation check failed')
                cursor = image_bytes
                for record in added:
                    _, compressed, _, _, _, _, width, height, offset, pitch, _, _ = record
                    if compressed not in (0, 1) or offset != cursor or pitch != width or width < 1 or height < 1:
                        raise ValueError('Invalid appended glyph bitmap reference')
                    if compressed:
                        packed = parsed['images'][offset:offset + 9]
                        if len(packed) < 9:
                            raise ValueError('Truncated appended glyph compression header')
                        decoded, stored = struct.unpack_from('<II', packed)
                        if decoded != width * height or stored < 9 or offset + stored > len(parsed['images']):
                            raise ValueError('Invalid appended compressed glyph extent')
                        alpha = dxa.decompress(parsed['images'][offset:offset + stored], decoded)
                        cursor += stored
                    else:
                        alpha = parsed['images'][offset:offset + width * height]
                        cursor += width * height
                    if not any(alpha) and not slots[record[0]].isspace():
                        raise ValueError('The appended font glyph contains no visible pixels')
                if cursor != len(parsed['images']):
                    raise ValueError('Appended glyph image range mismatch')
        if (source.stat().st_size != original_stat.st_size or source.stat().st_mtime_ns != original_stat.st_mtime_ns
                or file_sha256(source) != original_sha):
            raise ValueError('Original font archive changed during conversion')
        serialized = (json.dumps(codebook, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
        return {'codebook_sha256': _sha(serialized), 'verified_glyphs': len(slots),
                'font_members': len(rebuilt), 'source_sha256': original_sha, 'resource_sha256': file_sha256(output),
                'resource_bytes': output.stat().st_size, 'original_glyphs_and_bitmap_bytes_preserved': True,
                'source_archive_key_preserved': True, 'compact_archive': True,
                'backend': {'windows_font': 'Windows GDI private memory font',
                            'freetype_font': 'FreeType private memory font'}.get(
                                type(rasterizer).__module__, 'Custom test rasterizer'), 'pip_dependencies': False,
                'rendered_in_game': False, 'seconds': round(time.monotonic() - started, 3), 'members': reports}
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def build(source_font_bcx, codebook, output_font_bcx, font_path, *, progress=None):
    """Produce all 256 DFTs and return the converter's font verification contract.

    font_path must name the caller's Noto Sans KR TTF. Windows uses private
    memory GDI registration; Linux uses the system FreeType library and a
    private memory face. Neither backend installs fonts or requires pip.
    """
    expected = {f'{family}{size:02}.DFT' for family in ('KANJIFONT', 'MAINFONT') for size in range(1, 129)}
    try:
        renderer = renderer_for_platform()(font_path)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    with renderer as rasterizer:
        report = _rebuild(source_font_bcx, codebook, output_font_bcx, rasterizer,
                          expected_members=expected, progress=progress)
    report['private_font_removed'] = True
    return report
