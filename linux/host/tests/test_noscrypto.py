import unittest

from ntpl import noscrypto


class NoscryptoTests(unittest.TestCase):
    def test_pack_unpack_round_trip_ascii(self):
        data = b"walk 19 146 0 10"
        packed = noscrypto.pack(data)
        self.assertEqual(packed[-1], 0xFF)
        self.assertEqual(noscrypto.world_decrypt(packed), data)

    def test_pack_unpack_round_trip_mixed_bytes(self):
        data = bytes(range(1, 256))
        packed = noscrypto.pack(data, noscrypto.ENCRYPTION_TABLE)
        self.assertEqual(
            noscrypto.unpack(packed, noscrypto.ENCRYPTION_TABLE), data
        )

    def test_pack_rle_chunks(self):
        # Digits are packed two per byte; a long run is split at 0x7E.
        digits = b"1" * 200
        packed = noscrypto.pack(digits)
        self.assertEqual(noscrypto.world_decrypt(packed), digits)

    def test_pack_literal_chunk(self):
        data = b"~~~"
        packed = noscrypto.pack(data)
        # '~' is not in the table: a literal chunk of 3 bytes follows.
        self.assertEqual(noscrypto.world_decrypt(packed), data)

    def test_pack_stops_at_nul(self):
        self.assertEqual(noscrypto.pack(b"ab\x00cd"), noscrypto.pack(b"ab"))

    def test_login_encrypt_vector(self):
        self.assertEqual(
            noscrypto.login_encrypt(b"abc"),
            bytes((0xB1, 0xB0, 0xAF, 0xD8)),
        )
        # A trailing newline is not duplicated.
        self.assertEqual(
            noscrypto.login_encrypt(b"abc\n"),
            bytes((0xB1, 0xB0, 0xAF, 0xD8)),
        )

    def test_login_decrypt_is_the_documented_transform(self):
        self.assertEqual(
            noscrypto.login_decrypt(bytes((0xB1, 0xB0, 0xAF, 0xD8))),
            bytes((0xA2, 0xA1, 0xA0, 0xC9)),
        )

    def test_world_xor_styles(self):
        data = bytes((0x31, 0x32))
        session = 0x41  # (0x41 >> 6) & 3 == 1, key 0x41
        self.assertEqual(
            noscrypto.world_xor(data, session, first=True),
            bytes((0x40, 0x41)),  # 0x31 + 0x0F, 0x32 + 0x0F
        )
        self.assertEqual(
            noscrypto.world_xor(data, session),
            bytes((0xB0, 0xB1)),  # style 1: value - 0x41 - 0x40
        )

    def test_world_encrypt_uses_the_session_and_first_flag(self):
        packet = b"0 hello"
        first = noscrypto.world_encrypt(packet, 123, first=True)
        later = noscrypto.world_encrypt(packet, 123, first=False)
        self.assertNotEqual(first, later)
        self.assertEqual(first[-1], (noscrypto.pack(packet)[-1] + 0x0F) & 0xFF)


if __name__ == "__main__":
    unittest.main()
