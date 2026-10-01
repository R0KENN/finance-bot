"""Проверка самих проверок: стенд не должен пропускать то, что Telegram не примет."""
import unittest

from tests.harness import html_errors


class HtmlCheckTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(html_errors("<b>жирный</b> и <i>курсив</i> &lt;x&gt; <code>1</code>"), [])
        self.assertEqual(html_errors("<pre>таблица\n  a  b</pre><blockquote>цитата</blockquote>"), [])

    def test_unknown_tag(self):
        self.assertTrue(html_errors("<x>тег</x>"))
        self.assertTrue(html_errors("<script>alert(1)</script>"))

    def test_unbalanced(self):
        self.assertTrue(html_errors("<b>не закрыт"))
        self.assertTrue(html_errors("лишний</b>"))
        self.assertTrue(html_errors("<b><i>перепутан</b></i>"))


if __name__ == "__main__":
    unittest.main()
