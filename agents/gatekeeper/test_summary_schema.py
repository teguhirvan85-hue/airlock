import json
import unittest

from summary_schema import Refused, check, render

GOOD = {
    "title": "Coupon lets small orders ship free",
    "severity": "medium",
    "steps": ["Add one kettle (52000)", "Apply the 10% coupon", "Open checkout"],
    "expected": "Shipping fee is charged because the paid amount is under 50000",
    "actual": "Shipping is free and the total is 46800",
    "files": ["shop/cart.py"],
}


def with_(**changes):
    return json.dumps({**GOOD, **changes})


class CheckTest(unittest.TestCase):
    def test_good_summary_passes(self):
        self.assertEqual(check(json.dumps(GOOD))["title"], GOOD["title"])

    def test_render_lists_steps_and_files(self):
        card = render(check(json.dumps(GOOD)))
        self.assertIn("2. Apply the 10% coupon", card)
        self.assertIn("`shop/cart.py`", card)

    def test_refuses(self):
        cases = {
            "not json": "hello",
            "extra field": with_(note="hi"),
            "bad severity": with_(severity="urgent"),
            "no steps": with_(steps=[]),
            "link": with_(actual="see https://example.com"),
            "mention": with_(expected="ask @fixer"),
            "code fence": with_(actual="```sh```"),
            "markup": with_(title="<b>x</b>"),
            "hidden char": with_(title="Coupon​ bug"),
            "absolute path": with_(files=["/etc/hosts"]),
            "parent path": with_(files=["../x"]),
            "too long": with_(title="x" * 121),
        }
        for name, raw in cases.items():
            with self.subTest(name):
                with self.assertRaises(Refused):
                    check(raw)


if __name__ == "__main__":
    unittest.main()
