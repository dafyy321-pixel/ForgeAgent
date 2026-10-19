"""Independent behavioral checks for h11 PR 122; standard library only."""
import unittest

import h11


class EarlyInvalidHTTP(unittest.TestCase):
    def test_obviously_invalid_prefixes(self):
        for role in [h11.SERVER, h11.CLIENT]:
            for prefix in [b"\x00", b" ", b"\x16\x03\x01\x00\xa5", b"\x16\x03\x03\x00\x31"]:
                with self.subTest(role=role, prefix=prefix):
                    conn = h11.Connection(role)
                    conn.receive_data(prefix)
                    with self.assertRaises(h11.RemoteProtocolError):
                        conn.next_event()

    def test_valid_fragmented_request(self):
        conn = h11.Connection(h11.SERVER)
        self.assertIs(conn.next_event(), h11.NEED_DATA)
        for chunk in [b"G", b"ET / HTTP/1.1\r", b"\nHost: example.test\r\n"]:
            conn.receive_data(chunk)
            self.assertIs(conn.next_event(), h11.NEED_DATA)
        conn.receive_data(b"\r\n")
        request = conn.next_event()
        self.assertIsInstance(request, h11.Request)
        self.assertEqual(request.target, b"/")

    def test_valid_fragmented_response(self):
        conn = h11.Connection(h11.CLIENT)
        self.assertIs(conn.next_event(), h11.NEED_DATA)
        for chunk in [b"H", b"TTP/1.1 200 OK\r\n", b"Content-Length: 0\r\n"]:
            conn.receive_data(chunk)
            self.assertIs(conn.next_event(), h11.NEED_DATA)
        conn.receive_data(b"\r\n")
        self.assertIsInstance(conn.next_event(), h11.Response)


if __name__ == "__main__":
    unittest.main()
