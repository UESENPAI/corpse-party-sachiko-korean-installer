"""Extract one fingerprinted JDF from its reviewed 7z without external tools.

This is an exact-archive profile, not a general 7z reader.  The requested JDF
belongs to solid folder 0 (JPEG + INI + JDF), using LZMA1 alone.  The separate
EXE folder uses BCJ2 and is never decoded or executed.  Only offsets, sizes,
coder properties and digests are embedded; no archive/PSP/JDF bytes are stored.

Format reference: https://github.com/ip7z/7zip/blob/main/DOC/7zFormat.txt
"""
import hashlib
import lzma
import struct
import zlib

from sachiko_profile import PROFILE

PACKING = {
    'version': (0, 3),
    'packed_offset': 32,
    'packed_size': 11745620,
    'packed_sha256': 'e1f2db4e2770f91cef6da1e05d1fd00499a2182344bc8efa2e4fea8c5f1eb082',
    'coder_id': '030101',
    'lzma_properties': '5d00000001',
    'solid_size': 13175749,
    'solid_sha256': '4e32495bf8b48ebd97b9957c81ff8344f4e6a3cc00fdfe6d4725be32523722dd',
    'member_offset': 82949,
    'member_crc32': 0xd36f3f8e,
    'next_header_offset': 11810625,
    'next_header_size': 37,
    'next_header_sha256': '1f3d183ed698986618aca1b7bf38002f1ca1e475031139f867011a30d64f6cee',
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _lzma_filter(properties):
    """Decode the five-byte LZMA1 coder property (lc/lp/pb and LE dictionary)."""
    raw = bytes.fromhex(properties)
    if len(raw) != 5 or raw[0] >= 9 * 5 * 5:
        raise ValueError('Invalid reviewed LZMA1 coder properties')
    lc = raw[0] % 9
    remainder = raw[0] // 9
    lp, pb = remainder % 5, remainder // 5
    dictionary = int.from_bytes(raw[1:], 'little')
    if lc + lp > 4 or not 4096 <= dictionary <= 64 * 1024 * 1024:
        raise ValueError('Reviewed LZMA1 dictionary/properties exceed limits')
    return {'id': lzma.FILTER_LZMA1, 'lc': lc, 'lp': lp, 'pb': pb,
            'dict_size': dictionary}


def _decode_raw(packed, packing):
    expected = packing['solid_size']
    if type(expected) is not int or not 0 < expected <= 32 * 1024 * 1024:
        raise ValueError('Invalid reviewed solid-folder output bound')
    if packing['coder_id'] != '030101':
        raise ValueError('Unsupported reviewed solid-folder coder')
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW,
                                       filters=[_lzma_filter(packing['lzma_properties'])])
        # 7z LZMA1 can omit the end marker because its output size is known.
        # One extra output byte detects a false size bound.  For a no-marker
        # stream needs_input must be true, so no buffered output is ignored.
        output = decoder.decompress(packed, max_length=expected + 1)
    except lzma.LZMAError as exc:
        raise ValueError('Reviewed 7z LZMA1 stream could not be decoded') from exc
    if (len(output) != expected or decoder.unused_data
            or (not decoder.eof and not decoder.needs_input)):
        raise ValueError('Reviewed 7z solid-folder length/termination mismatch')
    if _sha(output) != packing['solid_sha256']:
        raise ValueError('Reviewed 7z solid-folder checksum mismatch')
    return output


def extract_jdf(archive):
    """Return the verified JDF from the exact known archive, with no I/O.

    Authentication precedes decompression. Unknown/repacked/truncated archives
    are refused. The output is bounded and verified by solid SHA256, member
    size/SHA256 and member CRC32. No bundled patch program is processed.
    """
    if not isinstance(archive, bytes):
        raise ValueError('7z input must be immutable bytes')
    if (len(archive) != PROFILE['archive']['size']
            or _sha(archive) != PROFILE['archive']['sha256']):
        raise ValueError('7z input does not match the reviewed archive checksum')
    if (len(archive) < 32 or archive[:6] != b'7z\xbc\xaf\x27\x1c'
            or tuple(archive[6:8]) != PACKING['version']):
        raise ValueError('Reviewed 7z signature/version mismatch')
    start_crc = struct.unpack_from('<I', archive, 8)[0]
    if zlib.crc32(archive[12:32]) & 0xffffffff != start_crc:
        raise ValueError('Reviewed 7z start-header CRC mismatch')
    next_offset, next_size, next_crc = struct.unpack_from('<QQI', archive, 12)
    header_offset = 32 + next_offset
    if (header_offset != PACKING['next_header_offset']
            or next_size != PACKING['next_header_size']
            or header_offset + next_size != len(archive)):
        raise ValueError('Reviewed 7z next-header span mismatch')
    header = archive[header_offset:header_offset + next_size]
    if (zlib.crc32(header) & 0xffffffff != next_crc
            or _sha(header) != PACKING['next_header_sha256']):
        raise ValueError('Reviewed 7z next-header checksum mismatch')
    offset, size = PACKING['packed_offset'], PACKING['packed_size']
    if not 32 <= offset < offset + size <= header_offset:
        raise ValueError('Reviewed 7z packed span is outside data area')
    packed = archive[offset:offset + size]
    if _sha(packed) != PACKING['packed_sha256']:
        raise ValueError('Reviewed 7z packed-stream checksum mismatch')
    solid = _decode_raw(packed, PACKING)
    start = PACKING['member_offset']
    end = start + PROFILE['jdf']['size']
    if not 0 <= start < end <= len(solid):
        raise ValueError('Reviewed JDF member is outside its solid folder')
    member = solid[start:end]
    if (_sha(member) != PROFILE['jdf']['sha256']
            or zlib.crc32(member) & 0xffffffff != PACKING['member_crc32']):
        raise ValueError('Extracted JDF does not match its reviewed checksum')
    return member
