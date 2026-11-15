"""Decode a profiled, completely known island of a reverse CRILAYLA stream.

The byte/bit layout was checked against the original implementations at
https://github.com/kamikat/cpktools/blob/master/cpk/crilayla.py and
https://github.com/wmltogether/CriPakTools . External LZ history always stays
invalid. No external dictionary, Korean wording, or game data is embedded.
"""


def decode_known_island(data, start, end, skip, max_output):
    if (not isinstance(data, bytes) or not 0 <= start < end <= len(data)
            or not 0 <= skip < 48 or not 0 < max_output <= 4 * 1024 * 1024):
        raise ValueError('Invalid CRILAYLA island bounds')
    bit = end * 8 - 1 - skip
    minimum = start * 8
    output, mask = bytearray(), bytearray()

    def bits(width):
        nonlocal bit
        if bit - width + 1 < minimum:
            raise EOFError
        value = 0
        for _ in range(width):
            value = (value << 1) | ((data[bit // 8] >> (bit % 8)) & 1)
            bit -= 1
        return value

    try:
        while len(output) < max_output:
            if not bits(1):
                output.append(bits(8))
                mask.append(1)
            else:
                distance, count = bits(13) + 3, 3
                for width in (2, 3, 5):
                    value = bits(width)
                    count += value
                    if value != (1 << width) - 1:
                        break
                else:
                    while True:
                        value = bits(8)
                        count += value
                        if value != 255:
                            break
                if len(output) + count > max_output:
                    raise ValueError('CRILAYLA output exceeds profile bounds')
                for _ in range(count):
                    if distance <= len(output):
                        output.append(output[-distance])
                        mask.append(mask[-distance])
                    else:
                        output.append(0)
                        mask.append(0)
    except EOFError:
        pass
    return bytes(reversed(output)), bytes(reversed(mask))
