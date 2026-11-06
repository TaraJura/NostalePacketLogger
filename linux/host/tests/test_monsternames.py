import unittest

from ntpl.monsternames import (
    decrypt_text_dat,
    default_lang_path,
    load_monster_names,
    load_skill_descriptions,
    parse_monster_text,
)


class MonsterTextTests(unittest.TestCase):
    def test_parse_records(self):
        text = (
            "zts1e\tBaby Fox\r"
            "zts200e\tMountain Turtle\r"
            "zts206e\tAngry Queen Bee\r"
            "garbage\r"
            "zts7e\t\r"
        )
        self.assertEqual(
            parse_monster_text(text),
            {
                1: "Baby Fox",
                200: "Mountain Turtle",
                206: "Angry Queen Bee",
            },
        )

    def test_decrypt_plain_run(self):
        # 0x03 = three literal bytes, XOR 0x33.
        data = bytes([0x03]) + bytes(
            ord(letter) ^ 0x33 for letter in "abc"
        )
        self.assertEqual(decrypt_text_dat(data), b"abc")

    def test_decrypt_nibble_run(self):
        # 0x82 = high-bit set, two characters from the alphabet. 0x12 is
        # high nibble 1 -> ' ' and low nibble 2 -> '-'.
        self.assertEqual(decrypt_text_dat(bytes([0x82, 0x12])), b" -")

    def test_carriage_return_marker(self):
        self.assertEqual(decrypt_text_dat(bytes([0xFF])), b"\r")


@unittest.skipUnless(
    default_lang_path().is_file(), "NosTale language data is not installed"
)
class InstalledLanguageDataTests(unittest.TestCase):
    def test_loads_known_monster_names(self):
        names = load_monster_names()
        self.assertGreater(len(names), 1000)
        self.assertEqual(names.get(1), "Baby Fox")
        self.assertEqual(names.get(200), "Mountain Turtle")
        self.assertEqual(names.get(206), "Angry Queen Bee")

    def test_loads_skill_descriptions(self):
        skills = load_skill_descriptions()
        self.assertGreater(len(skills), 1000)
        self.assertIn("Spell Gun", skills.get(876, ""))


if __name__ == "__main__":
    unittest.main()
