import unittest

from ntpl.spells import (
    default_nsgtd_path,
    load_spells,
    parse_skill_text,
)


class SkillParserTests(unittest.TestCase):
    def test_parses_one_active_record(self):
        text = (
            "#=========================================================\r"
            "\tVNUM\t876\r"
            "\tNAME\tzts562e\r"
            "\tTYPE\t1\t10\t38\t2\t0\t3\r"
            "\tTARGET\t0\t1\t11\t4\t0\r"
            "\tDATA\t0\t7\t0\t0\t7\t900\t0\t0\t145\t0\t0\t11\t4\t0\t0\r"
            "\tZ_DESC\t1\rzts563e\r"
            "#=========================================================\r"
        )
        spells = parse_skill_text(text, {562: "Holy Hammer"})
        spell = spells[876]
        self.assertEqual(spell.name, "Holy Hammer")
        self.assertTrue(spell.active)
        self.assertEqual(spell.target_mode, "monster")
        self.assertEqual(spell.range, 11)
        self.assertEqual(spell.cast_time, 0.7)
        self.assertEqual(spell.cooldown, 90.0)
        self.assertEqual(spell.mp_cost, 145)

    def test_self_target(self):
        text = (
            "#=========================================================\n"
            "\tVNUM\t869\n"
            "\tNAME\tzts548e\n"
            "\tTYPE\t1\t0\t38\t2\t0\t3\n"
            "\tTARGET\t1\t1\t0\t0\t0\n"
            "\tDATA\t0\t0\t0\t0\t10\t600\t0\t0\t300\t0\t0\t0\t0\t0\t0\n"
        )
        spell = parse_skill_text(text, {548: "Mana Shield"})[869]
        self.assertEqual(spell.target_mode, "self")
        self.assertEqual(spell.mp_cost, 300)
        self.assertEqual(spell.cooldown, 60.0)


@unittest.skipUnless(
    default_nsgtd_path().is_file(), "NosTale NSgtdData.NOS is not installed"
)
class InstalledSkillDataTests(unittest.TestCase):
    def test_known_spells(self):
        spells = load_spells()
        self.assertGreater(len(spells), 1000)
        self.assertGreater(sum(1 for s in spells.values() if s.active), 100)

        attack = spells[866]
        self.assertEqual(attack.name, "Holy Attack")
        self.assertEqual(
            (attack.mp_cost, attack.target_mode, attack.range),
            (0, "monster", 11),
        )
        self.assertAlmostEqual(attack.cooldown, 0.9)

        hammer = spells[876]
        self.assertEqual(hammer.name, "Holy Hammer")
        self.assertEqual(hammer.mp_cost, 145)
        self.assertAlmostEqual(hammer.cooldown, 90.0)
        self.assertAlmostEqual(hammer.cast_time, 0.7)

        shield = spells[869]
        self.assertEqual(shield.name, "Mana Shield")
        self.assertEqual(shield.target_mode, "self")
        self.assertEqual(shield.mp_cost, 300)


if __name__ == "__main__":
    unittest.main()
