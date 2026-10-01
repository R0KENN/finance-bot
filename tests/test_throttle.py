"""Антиспам: всплеск проходит, поток режется, лимит у каждого пользователя свой."""
import unittest

from finbot.middlewares import ThrottleMiddleware


class ThrottleTests(unittest.TestCase):
    def test_burst_then_block(self):
        t = ThrottleMiddleware()
        results = [t._allow(1, 0.0)[0] for _ in range(20)]
        self.assertEqual(results[:12], [True] * 12)
        self.assertEqual(results[12:], [False] * 8)

    def test_refill(self):
        t = ThrottleMiddleware()
        for _ in range(12):
            t._allow(1, 0.0)
        self.assertFalse(t._allow(1, 0.0)[0])
        self.assertTrue(t._allow(1, 1.0)[0])  # за секунду вернулось 2 запроса

    def test_users_independent(self):
        t = ThrottleMiddleware()
        for _ in range(12):
            t._allow(1, 0.0)
        self.assertFalse(t._allow(1, 0.0)[0])
        self.assertTrue(t._allow(2, 0.0)[0])

    def test_notice_rate_limited(self):
        t = ThrottleMiddleware()
        for _ in range(12):
            t._allow(1, 0.0)
        self.assertEqual(t._allow(1, 0.0), (False, True))
        self.assertEqual(t._allow(1, 0.1), (False, False))

    def test_prune(self):
        t = ThrottleMiddleware()
        t.PRUNE_ABOVE = 2
        for uid in (1, 2, 3):
            t._allow(uid, 0.0)
        t._allow(4, 100.0)
        self.assertEqual(set(t._buckets), {4})


if __name__ == "__main__":
    unittest.main()
