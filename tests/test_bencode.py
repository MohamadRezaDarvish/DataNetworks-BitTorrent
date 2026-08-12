import unittest

from common.bencode import bencode, bendecode


class TestBencode(unittest.TestCase):

    def test_integer(self):
        self.assertEqual(bencode(42), b"i42e")
        self.assertEqual(bendecode(b"i42e"), 42)

    def test_negative_integer(self):
        self.assertEqual(bencode(-5), b"i-5e")
        self.assertEqual(bendecode(b"i-5e"), -5)

    def test_bytes(self):
        self.assertEqual(bencode(b"spam"), b"4:spam")
        self.assertEqual(bendecode(b"4:spam"), b"spam")

    def test_empty_bytes(self):
        self.assertEqual(bencode(b""), b"0:")
        self.assertEqual(bendecode(b"0:"), b"")

    def test_list(self):
        data = [b"cat", 5]

        encoded = bencode(data)
        decoded = bendecode(encoded)

        self.assertEqual(encoded, b"l3:cati5ee")
        self.assertEqual(decoded, data)

    def test_dictionary(self):
        data = {
            b"name": b"hello.txt",
            b"length": 100,
        }

        encoded = bencode(data)
        decoded = bendecode(encoded)

        self.assertEqual(decoded, data)

    def test_nested_data(self):
        data = {
            b"name": b"test",
            b"files": [
                {
                    b"name": b"a.txt",
                    b"length": 123,
                },
                {
                    b"name": b"b.txt",
                    b"length": 456,
                },
            ],
        }

        self.assertEqual(
            bendecode(bencode(data)),
            data
        )

    def test_unsupported_type(self):
        with self.assertRaises(TypeError):
            bencode(3.14)

    def test_invalid_integer(self):
        with self.assertRaises(ValueError):
            bendecode(b"i123")

    def test_invalid_string_length(self):
        with self.assertRaises(ValueError):
            bendecode(b"5:abc")


    def test_empty_list(self):
        self.assertEqual(bencode([]), b"le")
        self.assertEqual(bendecode(b"le"), [])


    def test_empty_dict(self):
        self.assertEqual(bencode({}), b"de")
        self.assertEqual(bendecode(b"de"), {})


    def test_extra_data(self):
        with self.assertRaises(ValueError):
            bendecode(b"i5ei6e")


if __name__ == "__main__":
    unittest.main()