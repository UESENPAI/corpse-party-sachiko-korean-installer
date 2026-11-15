import struct
import unittest

import pc_bin
import pc_scene


def script(prefix):
    return prefix + b'\x0d\x02first\0second\0third\0' + b'\x90' + b'0123456789ABCDEF'


class SceneRelocationTests(unittest.TestCase):
    def test_moves_dialogue_and_choice_references_without_touching_other_arguments(self):
        prefix = bytearray(32 + 16 + 18 + 10 + 8)
        pool = len(prefix) + 2
        second, third = pool + 6, pool + 13
        struct.pack_into('<H', prefix, 28, 32)
        struct.pack_into('<BBHHIIH', prefix, 32, 0x18, 16, 0, 0xffff, second, second, 0)
        struct.pack_into('<BBHHHIHHBB', prefix, 48, 0x1b, 18, 1, 0, 0, third, 1, 0xffff, 0, 0xff)
        struct.pack_into('<BBHHI', prefix, 66, 0x1f, 10, 0, 0, 0xffffffff)
        struct.pack_into('<BBHI', prefix, 76, 0x24, 8, 0, 0)
        original = script(prefix)
        rebuilt = pc_bin.replace(original, {0: {'source': 'first', 'target': 'a longer line'}})
        fixed = pc_scene.relocate(original, rebuilt, 'BS01A.BIN', '778390')
        self.assertEqual(second + 8, struct.unpack_from('<I', fixed, 38)[0])
        self.assertEqual(third + 8, struct.unpack_from('<I', fixed, 56)[0])
        self.assertEqual(second, struct.unpack_from('<I', fixed, 42)[0])
        self.assertEqual(rebuilt[66:], fixed[66:])

    def test_updates_adjacent_short_dictionary_references_independently(self):
        prefix = bytearray(64)
        prefix[:4] = b'\x01\x01\xff\xff'
        struct.pack_into('<5H', prefix, 16, 66, 72, 79, 72, 79)
        original = script(prefix)
        rebuilt = pc_bin.replace(original, {0: {'source': 'first', 'target': 'first line'}})
        fixed = pc_scene.relocate(original, rebuilt, 'DICT.BIN', '778400')
        self.assertEqual((66, 77, 84, 77, 84), struct.unpack_from('<5H', fixed, 16))
        self.assertEqual(rebuilt[26:], fixed[26:])

    def test_rejects_dialogue_reference_inside_an_original_slot(self):
        prefix = bytearray(48)
        struct.pack_into('<H', prefix, 0, 32)
        struct.pack_into('<BBHHI', prefix, 32, 0x18, 16, 0, 0xffff, 51)
        original = script(prefix)
        rebuilt = pc_bin.replace(original, {0: {'source': 'first', 'target': 'first line'}})
        with self.assertRaisesRegex(ValueError, 'not a string start'):
            pc_scene.relocate(original, rebuilt, 'CP2U00A.BIN', '778400')
