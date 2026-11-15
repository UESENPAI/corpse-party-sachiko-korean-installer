"""Accept only the reviewed user-supplied 7z and recover its dialogue locally."""

import hashlib
import os
from pathlib import Path

from psp_korean_codebook import decode_sachiko, encode_sachiko
from sachiko_converter import recover_pools
from sachiko_profile import PROFILE
from sevenzip_input import extract_jdf

FILENAME = 'Corpse_party_2U_JPN.iso-jptch.7z'


def verified_archive(path):
    if path is None:
        raise ValueError('원 배포처에서 받은 PSP 한글패치를 --psp-patch로 지정하세요.')
    path = Path(path)
    expected = PROFILE['archive']
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected['size']:
        raise ValueError('확인된 PSP 한글패치 7z 파일의 크기와 일치하지 않습니다.')
    with path.open('rb') as stream:
        raw = stream.read(expected['size'] + 1)
    if len(raw) != expected['size'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
        raise ValueError('확인된 PSP 한글패치 7z 파일의 해시와 일치하지 않습니다.')
    return raw


def selected_patch(path):
    if path is not None:
        return path
    folders = [Path.home() / 'Downloads']
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        drive = Path(letter + ':/') if os.name == 'nt' else Path('/mnt') / letter.lower()
        if drive.is_dir():
            folders.append(drive / 'Downloads')
    for folder in folders:
        candidate = folder / FILENAME
        try:
            verified_archive(candidate)
        except (OSError, ValueError):
            continue
        return candidate
    raise ValueError('다운로드 폴더에서 확인된 PSP 한글패치를 찾지 못했습니다. --psp-patch로 지정하세요.')


def recover(archive):
    pools = recover_pools(extract_jdf(archive))
    result = {}
    for member, values in pools.items():
        for index, opaque in enumerate(values):
            korean = decode_sachiko(opaque)
            if encode_sachiko(korean) != opaque:
                raise ValueError('PSP glyph decoding failed its roundtrip')
            result[member, index] = korean
    return result


def target(record, donors):
    value = donors.get(tuple(record['donor']))
    if not value or hashlib.sha256(value.encode('utf-8')).hexdigest() != record['target_sha256']:
        raise ValueError('Recovered PSP translation differs from the verified plan')
    return value
