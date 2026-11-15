import unittest

from pc_bin import parse, replace
from recipe_apply import preserve_speaker_key


class SpeakerIdentityTests(unittest.TestCase):
    def test_known_key_survives_while_dialogue_and_later_tags_are_translated(self):
        key = '【NPC】'.encode('cp932')
        source = '【NPC】 Original 【reference】%K%P'.encode('cp932')
        target = '【등장인물】 번역 【참조】%K%P'
        self.assertEqual(('【NPC】 번역 【참조】%K%P', key),
                         preserve_speaker_key(source, target, (key,)))
        self.assertEqual((target, b''), preserve_speaker_key(source, target, (b'other',)))

    def test_missing_leading_tag_is_rejected_for_a_known_key(self):
        with self.assertRaisesRegex(ValueError, 'leading speaker key'):
            key = '【NPC】'.encode('cp932')
            preserve_speaker_key(key + b' Original', '번역 【등장인물】', (key,))

    def test_cp932_alias_keeps_original_bytes(self):
        key = bytes.fromhex('8179fa4a817a')
        source = key + b'Original'
        pool = source + b'\0'
        raw = b'\r\x02' + pool + b'\x90' * ((-len(pool)) % 4) + b'\0' * 16
        self.assertNotEqual(key, key.decode('cp932').encode('cp932'))
        target, prefix = preserve_speaker_key(source, '【NPC】Updated', (key,))
        output = replace(raw, {0: {'source': source.decode('cp932'), 'target': target,
                                   'verbatim_prefix': prefix}})
        self.assertEqual((key + b'Updated',), parse(output).strings)


if __name__ == '__main__':
    unittest.main()
