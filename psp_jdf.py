"""Symbolic JoJoDiff decoding without a PSP source image or patch EXE.

JoJoDiff: https://jojodiff.sourceforge.net/
Operation semantics independently checked against the original JanPatch C
implementation: https://github.com/janjongboom/janpatch . This module is a
separate Python implementation. Unknown source-copy bytes retain a zero mask;
their placeholder byte values must never be imported into a PC resource.
"""
import bisect


class SymbolicJDF:
    MAX_ADDRESS = 2 * 1024 * 1024 * 1024
    MAX_PATCH = 32 * 1024 * 1024
    MAX_SEGMENTS = 250000

    def __init__(self, patch):
        if not isinstance(patch, bytes) or not patch or len(patch) > self.MAX_PATCH:
            raise ValueError('Invalid JoJoDiff patch size/type')
        self.segments = []
        literals = bytearray()
        cursor = source = target = 0

        def take():
            nonlocal cursor
            if cursor >= len(patch):
                raise ValueError('Truncated JoJoDiff operation')
            value = patch[cursor]
            cursor += 1
            return value

        def length():
            code = take()
            if code <= 251:
                return code + 1
            if code == 252:
                return 253 + take()
            width = {253: 2, 254: 4}.get(code)
            if width is None:
                raise ValueError('Invalid JoJoDiff length code')
            value = 0
            for _ in range(width):
                value = (value << 8) | take()
            if value == 0:
                raise ValueError('Empty JoJoDiff length')
            return value

        while cursor < len(patch):
            if (patch[cursor] == 0xa7 and cursor + 1 < len(patch)
                    and 0xa2 <= patch[cursor + 1] <= 0xa6):
                operation = patch[cursor + 1]
                cursor += 2
            else:
                operation = 0xa6
            if operation in (0xa2, 0xa3, 0xa4):
                count = length()
                if operation == 0xa2:
                    source -= count
                elif operation == 0xa4:
                    source += count
                else:
                    self.segments.append((target, count, None))
                    source += count
                    target += count
            else:
                beginning = len(literals)
                while cursor < len(patch):
                    value = take()
                    if value != 0xa7:
                        literals.append(value)
                        continue
                    following = take()
                    if 0xa2 <= following <= 0xa6:
                        cursor -= 2
                        break
                    if following == 0xa7:
                        literals.append(0xa7)
                    else:
                        literals.extend((0xa7, following))
                count = len(literals) - beginning
                if count:
                    self.segments.append((target, count, beginning))
                target += count
                if operation == 0xa6:
                    source += count
            if not 0 <= source <= self.MAX_ADDRESS or target > self.MAX_ADDRESS:
                raise ValueError('JoJoDiff source/target address exceeds bounds')
            if len(self.segments) > self.MAX_SEGMENTS:
                raise ValueError('Too many JoJoDiff segments')
        self.size = target
        self.literals = bytes(literals)
        self.starts = [segment[0] for segment in self.segments]

    def read(self, offset, size):
        if (not isinstance(offset, int) or not isinstance(size, int)
                or offset < 0 or size < 0 or size > self.MAX_PATCH
                or offset + size > self.size):
            raise ValueError('Invalid symbolic target range')
        data = bytearray(size)
        mask = bytearray(size)
        index = max(0, bisect.bisect_right(self.starts, offset) - 1)
        for start, count, literal in self.segments[index:]:
            if start >= offset + size:
                break
            first, end = max(start, offset), min(start + count, offset + size)
            if literal is not None and end > first:
                source = literal + first - start
                data[first - offset:end - offset] = self.literals[source:source + end - first]
                mask[first - offset:end - offset] = b'\1' * (end - first)
        return bytes(data), bytes(mask)

    def known(self, offset, size):
        data, mask = self.read(offset, size)
        if not all(mask):
            raise ValueError('PSP source-copy bytes are unavailable in the selected target span')
        return data
