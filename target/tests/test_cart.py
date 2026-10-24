import unittest

from shop.cart import SHIPPING_FEE, Cart


class CartTest(unittest.TestCase):
    def test_small_order_pays_shipping(self):
        cart = Cart()
        cart.add("tea", 12_000)
        self.assertEqual(cart.total(), 12_000 + SHIPPING_FEE)

    def test_large_order_ships_free(self):
        cart = Cart()
        cart.add("kettle", 60_000)
        self.assertEqual(cart.total(), 60_000)

    def test_coupon_takes_percent_off(self):
        cart = Cart(coupon_percent=10)
        cart.add("kettle", 80_000)
        self.assertEqual(cart.discount(), 8_000)

    def test_qty_must_be_positive(self):
        with self.assertRaises(ValueError):
            Cart().add("tea", 12_000, qty=0)


if __name__ == "__main__":
    unittest.main()
