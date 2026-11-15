"""Decode the supplied Sachiko patch's KS X 1001 Hangul in JIS level-one slots.

The bytes are not CP949: the 2,350 EUC-KR Hangul characters occupy successive
valid CP932 two-byte character slots beginning at 0x889f. Speaker names inside
the leading Japanese name brackets are retained as they appear in the donor.
This module includes no original or translated game text.
"""
import re


def make_sachiko_codebook():
    korean = []
    for high in range(0xb0, 0xc9):
        for low in range(0xa1, 0xff):
            value = bytes((high, low)).decode('euc_kr')
            if len(value) != 1 or not 0xac00 <= ord(value) <= 0xd7a3:
                raise ValueError('Expected KS X 1001 Hangul')
            korean.append(value)
    japanese = []
    for high in [*range(0x88, 0xa0), *range(0xe0, 0xef)]:
        for low in [*range(0x40, 0x7f), *range(0x80, 0xfd)]:
            if high * 256 + low < 0x889f:
                continue
            try:
                value = bytes((high, low)).decode('cp932')
            except UnicodeError:
                continue
            if len(value) == 1:
                japanese.append(value)
            if len(japanese) == len(korean):
                result = dict(zip(japanese, korean))
                assert len(result) == len(korean) == 2350
                return result
    raise ValueError('Insufficient CP932 glyph slots')


SACHIKO = make_sachiko_codebook()


def decode_sachiko(text):
    name = re.match(r'^【[^】]*】', text)
    boundary = name.end() if name else 0
    return text[:boundary] + ''.join(SACHIKO.get(c, c) for c in text[boundary:])


def encode_sachiko(text):
    name = re.match(r'^【[^】]*】', text)
    boundary = name.end() if name else 0
    inverse = {value: key for key, value in SACHIKO.items()}
    return text[:boundary] + ''.join(inverse.get(c, c) for c in text[boundary:])
