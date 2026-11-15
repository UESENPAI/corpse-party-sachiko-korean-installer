"""Relocate scene string references for the verified Japanese Steam programs."""
import re
import struct

import pc_bin


# These original records advertise a different length than the bytes they
# occupy. The recipe authenticates the complete original member before use.
_RECORD_LENGTHS = {
    ('778390', 'BS02A.BIN', 41198): (b'\x08\x12', 24),
    ('778390', 'BS02B.BIN', 18610): (b'\x08\x1e', 24),
    ('778390', 'DMENU.BIN', 1732): (b'\x1b\x6c', 68),
    ('778390', 'DMENU.BIN', 1812): (b'\x08\x42', 48),
    ('778400', 'DMENU.BIN', 1078): (b'\x08\x42', 60),
}
_ALIGNMENT = {
    ('778390', 'DMENU2.BIN'): 2154,
    ('778400', 'DMENU2.BIN'): 2482,
    ('778390', 'DMENU6.BIN'): 1706,
    ('778400', 'DMENU6.BIN'): 1706,
}


def _u16(raw, at):
    return struct.unpack_from('<H', raw, at)[0]


def _u32(raw, at):
    return struct.unpack_from('<I', raw, at)[0]


def _dict_offsets(raw, end):
    if end % 32:
        raise ValueError('Invalid DICT.BIN table size')
    for row in range(0, end, 32):
        if not any(raw[row:end]):
            return
        if raw[row:row + 4] != b'\x01\x01\xff\xff' or any(raw[row + 26:row + 32]):
            raise ValueError('Invalid DICT.BIN record')
        yield from range(row + 16, row + 26, 2)
    raise ValueError('Missing DICT.BIN table terminator')


def _shortcut_offsets(raw, end):
    table_end = 3 * 0x120
    if end <= table_end or _u16(raw, 2) != 0x3e0 or any(raw[table_end:0x3e0]):
        raise ValueError('Invalid SHORTCUT.BIN program table')
    for row in range(0, table_end, 0x120):
        if _u16(raw, row) != 1 or not 0x3e0 <= _u16(raw, row + 2) < end:
            raise ValueError('Invalid SHORTCUT.BIN record')
        yield row + 0x12
        yield row + 0x14


def _external_choices(raw, member, start, count):
    directory = 4 if member == 'DMENU2.BIN' else 2
    table = _u16(raw, directory)
    if not directory < table < start or (table - directory) % 4:
        raise ValueError('Invalid scene choice table directory')
    expected_start = table
    for row in range(directory, table, 4):
        first, last = struct.unpack_from('<2H', raw, row)
        if first != expected_start or last != first + count * 10 or last > start:
            raise ValueError('Invalid external scene choice bounds')
        for option in range(first, last, 10):
            if raw[option + 8]:
                raise ValueError('Unsupported external scene choice flags')
            yield option
        # Each ten-byte option has a separate six-byte branch descriptor.
        expected_start = last + count * 6
    if expected_start != start and (start - expected_start != 2
                                    or raw[expected_start:start] != b'\x90\x90'):
        raise ValueError('Scene choice table does not end at the program entry')


def _command_offsets(raw, member, app_id, start, end):
    at = start
    while at < end:
        if end - at < 2:
            raise ValueError(f'Truncated {member} command header')
        header = raw[at:at + 2]
        if at == _ALIGNMENT.get((app_id, member)) and header == b'\x90\x90':
            at += 2
            continue
        opcode, size = header
        original_header, actual_size = _RECORD_LENGTHS.get((app_id, member, at), (header, size))
        if header != original_header or actual_size < 2 or actual_size % 2 or at + actual_size > end:
            raise ValueError(f'Invalid {member} command at {at:#x}')
        if opcode == 0x18:
            if actual_size < 16:
                raise ValueError('Truncated scene message')
            yield at + 6
        elif opcode == 0x1f:
            if actual_size < 10:
                raise ValueError('Truncated synchronized scene message')
            if _u32(raw, at + 6) != 0xffffffff:
                yield at + 6
        elif opcode == 0x1b:
            if actual_size < 8:
                raise ValueError('Truncated scene choice')
            count, _, flags = struct.unpack_from('<3H', raw, at + 2)
            if flags & 0x100:
                if member not in {'DMENU2.BIN', 'DMENU6.BIN'} or actual_size != 10:
                    raise ValueError('Unsupported external scene choice')
                yield from _external_choices(raw, member, start, count)
            else:
                if actual_size == size and actual_size != 8 + count * 10:
                    raise ValueError('Invalid inline scene choice size')
                for option in range(at + 8, at + actual_size, 10):
                    if not raw[option + 8] & 2:
                        yield option
        elif opcode == 0x1e:
            if actual_size < 6:
                raise ValueError('Truncated scene popup')
            if _u16(raw, at + 4) != 0x8000:
                yield at + 2
            if actual_size >= 18 and _u16(raw, at + 8) and _u32(raw, at + 10):
                yield at + 10
                yield at + 14
        elif opcode == 0x24:
            if actual_size < 8:
                raise ValueError('Truncated scene message log command')
            if _u32(raw, at + 4):
                yield at + 4
        elif app_id == '778390' and opcode in (0xd8, 0xd9):
            if actual_size < 6:
                raise ValueError('Truncated scene movie command')
            yield at + 2
        at += actual_size


def pointer_offsets(script, member, app_id):
    """Return the verified 32-bit and 16-bit text field locations."""
    app_id = str(app_id)
    if app_id not in {'778390', '778400'}:
        raise ValueError('Unsupported scene game layout')
    raw = script.prefix
    end = script.pool_offset - 2
    if member == 'DICT.BIN':
        return (), tuple(_dict_offsets(raw, end))
    if member == 'SHORTCUT.BIN':
        return (), tuple(_shortcut_offsets(raw, end))
    if end < 32:
        raise ValueError(f'Truncated {member} scene header')
    start = _u16(raw, 0)
    if (re.fullmatch(r'(?:BS[0-9]{2}[A-Z]|BSXXX|CP2U(?:[0-9]{2}[A-Z]?|EX)|MAIN[0-9]{2})\.BIN', member)
            or member == 'CLRFLG.BIN'):
        entries = struct.unpack_from('<16H', raw)
        start = min((entry for entry in entries if 32 <= entry < end), default=0)
    elif member not in {'CP2UNOTE.BIN', 'DMENU.BIN', 'DMENU2.BIN', 'DMENU4.BIN', 'DMENU5.BIN', 'DMENU6.BIN'}:
        raise ValueError(f'Unsupported localized scene member: {member}')
    if not 32 <= start < end or start % 2:
        raise ValueError(f'Invalid {member} scene entry point')
    offsets = list(_command_offsets(raw, member, app_id, start, end))
    if app_id == '778400' and member == 'DMENU.BIN':
        table = tuple(range(30, 398, 8))
        starts = pc_bin._pool_starts(script)
        if (start != 500 or len(starts) < len(table)
                or any(struct.unpack_from('<2I', raw, row) != (slot, 0)
                       for row, slot in zip(table, starts))):
            raise ValueError('Invalid DMENU.BIN label table')
        offsets.extend(table)
    return tuple(offsets), ()


def relocate(original, rebuilt, member, app_id):
    if original == rebuilt:
        return rebuilt
    if len(rebuilt) >= 0x3ff00:
        raise ValueError(f'{member} exceeds the native scene bank')
    pointers, short_pointers = pointer_offsets(pc_bin.parse(original), member, app_id)
    return pc_bin.relocate_pool_pointers(
        original, rebuilt, pointers, short_offsets=short_pointers, preserved_values=())
