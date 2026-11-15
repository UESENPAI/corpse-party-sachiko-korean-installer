"""Independent FNTF reader and native-size, append-only glyph writer.

Layout: DXLib DxFont.h FONTDATAFILE structures. Existing press data, character
records (including aliases), and bitmap bytes are retained verbatim. No game
font or translation bytes are included in this source module.
"""
import struct
import re
from converter_dxa import decompress

RECORD = struct.Struct('<IBBhhhHHIIII')
PRESS_SIZE = 344


def dx_literals(data):
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    marker = min(range(256), key=counts.__getitem__)
    escaped = data.replace(bytes([marker]), bytes([marker, marker]))
    return struct.pack('<IIB', len(data), len(escaped) + 9, marker) + escaped


def dx_compress(data):
    """Encode repeated alpha/zero bytes with standard DX distance-one tokens.

    Original glyph data is retained verbatim. Only the newly generated masks
    and rebuilt metadata use this encoder; DXLib's existing decoder reads it.
    """
    marker = 254
    token = bytes([marker])
    stream = bytearray()
    cursor = 0
    for run in re.finditer(rb'(.)\1{4,}', data, re.DOTALL):
        start, end = run.span()
        # One literal is the dictionary source for an overlapping distance-one
        # reference. The remaining count is bounded by the DX count encoding.
        stream.extend(data[cursor:start + 1].replace(token, token + token))
        remaining = end - start - 1
        while remaining >= 4:
            count = min(remaining, 8195)
            length = count - 4
            code = ((length & 31) << 3) | (4 if length >= 32 else 0)
            stored = code + int(code >= marker)
            stream.extend((marker, stored))
            if length >= 32:
                stream.append(length >> 5)
            stream.append(0)  # stored distance + 1 == 1
            remaining -= count
        if remaining:
            stream.extend(data[end - remaining:end].replace(token, token + token))
        cursor = end
    stream.extend(data[cursor:].replace(token, token + token))
    return struct.pack('<IIB', len(data), len(stream) + 9, marker) + stream


def parse(data):
    if len(data) < 33 or data[:4] != b'FNTF':
        raise ValueError('Not an FNTF resource')
    _, version, padding, image_offset, maximum, count, count_ex = struct.unpack_from('<4sHHIIII', data)
    if version != 0 or not 33 <= image_offset <= len(data) or count > 65536:
        raise ValueError('Unsupported FNTF header')
    header = decompress(data[24:image_offset], PRESS_SIZE + count * RECORD.size)
    records = [RECORD.unpack_from(header, PRESS_SIZE + i * RECORD.size) for i in range(count)]
    return {'version': version, 'padding': padding, 'max_image_bytes': maximum,
            'count': count, 'count_ex': count_ex, 'press': header[:PRESS_SIZE],
            'records': records, 'images': data[image_offset:], 'image_offset': image_offset,
            'size': struct.unpack_from('<H', header, 258)[0],
            'height': struct.unpack_from('<H', header, 260)[0],
            'bit_depth': header[276], 'char_code_format': struct.unpack_from('<H', header, 270)[0]}


def record_bytes(records):
    return b''.join(RECORD.pack(*record) for record in records)


def append_native(data, slots, raster):
    """Append PUA glyphs using raster(char,size)->(width,height,8-bit alpha).

    Native GDI dimensions are kept, with no Python pixel resampling. The old
    full-width advance and vertical cell center determine drawing placement.
    """
    font = parse(data)
    if font['bit_depth'] != 2 or font['char_code_format'] != 932 or not 1 <= font['size'] <= 512:
        raise ValueError('Expected an 8-bit CP932 FNTF font')
    existing = {record[0]: record for record in font['records']}
    if set(slots) & set(existing):
        raise ValueError('Refusing to replace existing font glyphs')
    sample = next((existing[code] for code in (0x3042, 0x4e00, 0x30a2, 0xff21) if code in existing), None)
    if sample is None:
        raise ValueError('No original full-width glyph metric')
    advance, cell_height = max(sample[5], 1), max(sample[7], 1)
    images = bytearray(font['images'])
    records = list(font['records'])
    maximum = font['max_image_bytes']
    for slot, character in sorted(slots.items()):
        if not 0xe000 <= slot <= 0xf8ff or len(character) != 1:
            raise ValueError('Invalid PUA glyph mapping')
        width, height, alpha = raster(character, font['size'])
        if (not isinstance(alpha, bytes) or not 1 <= width <= 1024 or not 1 <= height <= 1024
                or len(alpha) != width * height or (not any(alpha) and not character.isspace())):
            raise ValueError('Missing or invalid Korean glyph bitmap')
        draw_x = (advance - width) // 2
        draw_y = sample[4] + (cell_height - height) // 2
        packed = dx_compress(alpha)
        compressed = int(len(packed) < len(alpha))
        records.append((slot, compressed, 0, draw_x, draw_y, advance, width, height, len(images), width, 0, 0))
        images.extend(packed if compressed else alpha)
        maximum = max(maximum, len(alpha))
    metadata = font['press'] + record_bytes(records)
    encoded = dx_compress(metadata)
    result = struct.pack('<4sHHIIII', b'FNTF', font['version'], font['padding'], 24 + len(encoded),
                         maximum, len(records), font['count_ex']) + encoded + images
    check = parse(result)
    if (check['press'] != font['press'] or check['records'][:font['count']] != font['records']
            or check['images'][:len(font['images'])] != font['images']):
        raise ValueError('Original glyph records or bitmap bytes changed')
    return result, font, check
