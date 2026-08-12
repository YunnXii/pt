import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.box_rss import BoxRssError, fetch_rss


class BoxRssTests(unittest.TestCase):
    def _resp(self, body: bytes, status=200, content_type="application/rss+xml; charset=utf-8"):
        return SimpleNamespace(
            status_code=status,
            content=body,
            headers={"content-type": content_type},
        )

    @patch("app.box_rss.httpx.get")
    def test_utf8_bom_and_whitespace_are_accepted(self, get):
        xml = b'\xef\xbb\xbf  \n<?xml version="1.0" encoding="UTF-8"?><rss><channel><title>M-Team</title><item><title>A</title><guid>123</guid></item></channel></rss>'
        get.return_value = self._resp(xml)
        out = fetch_rss("https://example.invalid/rss?token=secret")
        self.assertEqual(out["title"], "M-Team")
        self.assertEqual(out["items"][0]["id"], "123")

    @patch("app.box_rss.httpx.get")
    def test_html_response_has_clear_error_without_url(self, get):
        get.return_value = self._resp(b"<!doctype html><html><body>login</body></html>", content_type="text/html")
        with self.assertRaises(BoxRssError) as ctx:
            fetch_rss("https://example.invalid/rss?token=VERY_PRIVATE")
        msg = str(ctx.exception)
        self.assertIn("HTML", msg)
        self.assertNotIn("VERY_PRIVATE", msg)

    @patch("app.box_rss.httpx.get")
    def test_plain_text_response_is_not_sent_to_xml_parser(self, get):
        get.return_value = self._resp(b"rate limited", content_type="text/plain")
        with self.assertRaises(BoxRssError) as ctx:
            fetch_rss("https://example.invalid/rss")
        self.assertIn("不是可解析 XML", str(ctx.exception))

    @patch("app.box_rss.httpx.get")
    def test_malformed_xml_has_diagnostic(self, get):
        get.return_value = self._resp(b"<rss><channel><title>x</title></rss>")
        with self.assertRaises(BoxRssError) as ctx:
            fetch_rss("https://example.invalid/rss")
        self.assertIn("RSS XML 解析失败", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
