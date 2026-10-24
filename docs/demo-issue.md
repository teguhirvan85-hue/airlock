# Demo issue

Post this in the `airlock` channel, starting with the mention:

```
@airlock-reader New issue from GitHub:

Coupon makes small orders ship free

Steps: add one kettle (52000) to the cart, apply the 10% coupon, open checkout.
Expected: shipping fee 4900 is charged, because the customer pays 46800, under the 50000 free-shipping line.
Actual: shipping is free and the total is 46800.
Seen in shop/cart.py, I think the shipping check uses the price before the discount.
```
