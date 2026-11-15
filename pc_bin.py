"""Local PC BoS/2U BIN pool research; no game files are changed by this module.

Written from the installed binary layout. This does not embed or depend on the
GPL corpse-tools implementation. The marker/pool observation agrees with that
tool's public documented text replacement workflow, but boundaries are checked
and the 16-byte footer is explicitly retained here.
"""
from dataclasses import dataclass
import hashlib
import re
import struct


@dataclass(frozen=True)
class Script:
    prefix: bytes
    strings: tuple[bytes, ...]
    padding: bytes
    footer: bytes

    @property
    def pool_offset(self):
        return len(self.prefix)


def parse(raw):
    marker = raw.rfind(b"\x0d\x02")
    if marker < 0 or len(raw) < marker + 18:
        raise ValueError("No verified BIN pool boundary")
    prefix = raw[:marker + 2]
    pool = raw[marker + 2:-16]
    content = pool.rstrip(b"\x90")
    padding = pool[len(content):]
    if len(pool) % 4 or len(padding) > 3:
        raise ValueError("Invalid BIN pool alignment/padding")
    if content and not content.endswith(b"\0"):
        raise ValueError("Unterminated BIN string pool")
    strings = tuple(content[:-1].split(b"\0")) if content else ()
    for item in strings:
        text = item.decode("cp932", "strict")
        if any(ord(c) < 32 and c not in "\t\n\r" for c in text):
            raise ValueError("Unexpected control byte in BIN string pool")
    return Script(prefix, strings, padding, raw[-16:])


def control_signature(text, ignore_linebreaks=False):
    return (tuple(token for token in re.findall(r"%[A-Za-z][0-9]*", text)
                  if not ignore_linebreaks or token != "%N"),
            text.count("【"), text.count("】"))


def pua_codebook(characters, occupied=(), force=()):
    """Map non-CP932 characters to unused CP932 user-defined glyph slots.

    A DFT containing a Korean glyph at the resulting Unicode PUA is required.
    This changes storage only; it makes no claim about rendered output.
    """
    occupied = set(occupied)
    force = set(force)
    available = []
    for lead in range(0xf0, 0xfa):
        for trail in list(range(0x40, 0x7f)) + list(range(0x80, 0xfd)):
            code = (lead << 8) | trail
            char = code.to_bytes(2, "big").decode("cp932")
            if char not in occupied:
                available.append(code)
    need = []
    for char in sorted(set(characters)):
        if char in force:
            need.append(char)
            continue
        try:
            char.encode("cp932")
        except UnicodeEncodeError:
            need.append(char)
    if len(need) > len(available):
        raise ValueError("Not enough unused CP932 user-defined slots")
    return {char: available[i] for i, char in enumerate(need)}


def encode(text, codebook):
    result = bytearray()
    for char in text:
        if char in codebook:
            code = codebook[char]
            if not isinstance(code, int) or not 0xf040 <= code <= 0xf9fc:
                raise ValueError("Custom code must use a CP932 user-defined slot")
            stored = code.to_bytes(2, "big")
            stored.decode("cp932", "strict")
            result.extend(stored)
        else:
            result.extend(char.encode("cp932", "strict"))
    if b"\0" in result or b"\r\x02" in result:
        raise ValueError("Target contains a structural BIN delimiter")
    return bytes(result)


def decode(data, codebook):
    inverse = {code.to_bytes(2, "big").decode("cp932"): char
               for char, code in codebook.items()}
    return "".join(inverse.get(char, char) for char in data.decode("cp932"))


def replace(raw, replacements, codebook=None, allow_reflow=False):
    """Replace verified index/source records; return a new BIN byte string.

    Replacements are {index: {source: original_unicode, target: korean_unicode}}.
    Original bytecode, string order/count, and footer remain unchanged. Percent
    commands and speaker marker counts must remain identical. Line reflow is a
    separate future step, so %N is also preserved here.
    """
    codebook = codebook or {}
    script = parse(raw)
    strings = list(script.strings)
    inverse = {code.to_bytes(2, "big").decode("cp932"): char for char, code in codebook.items()}
    for index, change in replacements.items():
        if not isinstance(index, int) or not 0 <= index < len(strings):
            raise ValueError("BIN replacement index outside string pool")
        original = strings[index]
        source = original.decode("cp932")
        if source != change["source"]:
            raise ValueError("BIN replacement source mismatch")
        target = change["target"]
        if (control_signature(source, allow_reflow)
                != control_signature(target, allow_reflow)):
            raise ValueError("BIN replacement changes formatting/control commands")
        prefix = change.get("verbatim_prefix", b"")
        if not isinstance(prefix, bytes) or not original.startswith(prefix):
            raise ValueError("BIN verbatim prefix differs from original")
        prefix_text = prefix.decode("cp932", "strict")
        if not target.startswith(prefix_text):
            raise ValueError("BIN verbatim prefix differs from target")
        body = target[len(prefix_text):]
        encoded = encode(body, codebook)
        if "".join(inverse.get(c, c) for c in encoded.decode("cp932")) != body:
            raise ValueError("BIN target encoding did not roundtrip")
        strings[index] = prefix + encoded
    pool = b"".join(s + b"\0" for s in strings)
    padding = b"\x90" * ((-len(pool)) % 4)
    result = script.prefix + pool + padding + script.footer
    check = parse(result)
    if (check.prefix != script.prefix or check.footer != script.footer
            or len(check.strings) != len(script.strings)):
        raise ValueError("BIN replacement changed structural data")
    return result


def _pool_starts(script):
    cursor = script.pool_offset
    starts = []
    for value in script.strings:
        starts.append(cursor)
        cursor += len(value) + 1
    return starts


def relocate_pool_pointers(original, rebuilt, pointer_offsets, *, short_offsets=(), preserved_values=(0,)):
    """Relocate only caller-verified little-endian file offsets into the BIN pool.

    Each live pointer must address the start of an original NUL slot. Slot
    identity, including empty or equal strings, is preserved by ordinal.
    Extra sentinel values require an explicit format-specific profile.
    """
    before = parse(original)
    after = parse(rebuilt)
    if (before.prefix != after.prefix or before.footer != after.footer
            or len(before.strings) != len(after.strings)):
        raise ValueError("BIN pointer relocation requires the original slot layout")

    starts = dict(zip(_pool_starts(before), _pool_starts(after), strict=True))
    new_starts = set(starts.values())
    preserved_values = tuple(preserved_values)
    if any(type(value) is not int or not 0 <= value <= 0xffffffff
           or value in starts or value in new_starts for value in preserved_values):
        raise ValueError("Invalid BIN pointer sentinel")
    sentinels = set(preserved_values)

    offsets = [(at, 4) for at in pointer_offsets] + [(at, 2) for at in short_offsets]
    prefix_end = before.pool_offset - 2  # Exclude the pool delimiter itself.
    if any(type(at) is not int or not 0 <= at <= prefix_end - width for at, width in offsets):
        raise ValueError("BIN pointer location is outside the program prefix")
    offsets.sort()
    if any(at + width > right for (at, width), (right, _) in zip(offsets, offsets[1:])):
        raise ValueError("BIN pointer locations overlap")

    relocations = []
    for at, width in offsets:
        fmt = "<I" if width == 4 else "<H"
        old_offset = struct.unpack_from(fmt, before.prefix, at)[0]
        if old_offset in sentinels:
            continue
        if old_offset not in starts:
            raise ValueError(f"BIN pointer at {at:#x} is not a string start")
        new_offset = starts[old_offset]
        if new_offset >= 1 << (8 * width):
            raise ValueError(f"BIN pointer at {at:#x} exceeds its {8 * width}-bit field")
        relocations.append((fmt, at, new_offset))

    result = bytearray(rebuilt)
    for fmt, at, new_offset in relocations:
        struct.pack_into(fmt, result, at, new_offset)
    return bytes(result)


_DATA_TEXT_TABLES = (
    (0x24, 16, (0, 4, 8)),  # Speaker key, display name, and alternate display name.
    (0x28, 24, (0, 4, 8)),
    (0x30, 8, (0, 4)),
    (0x5c, 4, (0,)),
    (0x68, 16, (0, 4)),
)


def _data_bin_layout(script, app_id):
    app_id = str(app_id)
    if app_id not in ('778390', '778400'):
        raise ValueError("Unsupported DATA.BIN game layout")
    book_of_shadows = app_id == '778390'
    prefix_end = script.pool_offset - 2
    if prefix_end < 0x74:
        raise ValueError("Truncated DATA.BIN section directory")

    # Header offsets 0x20..0x70 define ordered section starts. Prefix-only
    # tables (including 0x20, 0x44 and 0x4c) bound the selected tables but
    # never contribute relocation locations.
    section_starts = struct.unpack_from("<21I", script.prefix, 0x20)
    if (section_starts[0] < 0x74 or section_starts[-1] >= prefix_end
            or any(left >= right for left, right in zip(section_starts, section_starts[1:]))):
        raise ValueError("Invalid DATA.BIN section bounds")
    section_ends = section_starts[1:] + (prefix_end,)

    tables = _DATA_TEXT_TABLES + (
        (0x60, 8 if book_of_shadows else 4, (0,)),
        (0x64, 4 if book_of_shadows else 8, (0,)),
    )
    if book_of_shadows:
        tables += ((0x70, 8, (4,)),)

    pool_indices = {start: index for index, start in enumerate(_pool_starts(script))}
    pointer_offsets = []
    speaker_keys = []
    for header, stride, columns in tables:
        section_index = (header - 0x20) // 4
        start, end = section_starts[section_index], section_ends[section_index]
        short_sentinel = header == 0x70
        terminator_size = 2 if short_sentinel else 4
        terminator_format = "<H" if short_sentinel else "<I"
        sentinel = 0xffff if short_sentinel else 0

        # Sachiko's 0x64 table shares the initial zero word of the empty
        # 0x68 table. All other reviewed terminators end their own section.
        shared_sentinel = not book_of_shadows and header == 0x64
        stop = end if shared_sentinel else end - terminator_size
        if (stop < start or (stop - start) % stride or stop + terminator_size > prefix_end
                or struct.unpack_from(terminator_format, script.prefix, stop)[0] != sentinel):
            raise ValueError(f"Invalid DATA.BIN table bounds or terminator at {header:#x}")

        for row in range(start, stop, stride):
            if struct.unpack_from(terminator_format, script.prefix, row)[0] == sentinel:
                raise ValueError(f"Premature DATA.BIN table terminator at {header:#x}")
            for column in columns:
                at = row + column
                value = struct.unpack_from("<I", script.prefix, at)[0]
                if value and value not in pool_indices:
                    raise ValueError(f"DATA.BIN pointer at {at:#x} is not a string start")
                pointer_offsets.append(at)
                if header == 0x24 and column == 0:
                    speaker_keys.append(pool_indices[value])
    return tuple(sorted(pointer_offsets)), tuple(speaker_keys)


def data_bin_pointer_offsets(raw, app_id):
    """Return only positions whose native DATA.BIN fields point into the pool."""
    return _data_bin_layout(parse(raw), app_id)[0]


def data_speaker_key_indices(raw, app_id):
    """Return original speaker key slot ordinals in native row order."""
    return _data_bin_layout(parse(raw), app_id)[1]


def relocate_data_bin(original, rebuilt, app_id):
    return relocate_pool_pointers(original, rebuilt, data_bin_pointer_offsets(original, app_id))


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()
