import concurrent.futures
import unittest

from tests.flash_sandbox import InventoryRoom, Scenario, cases_for


class InventoryRoomTests(unittest.TestCase):
    def test_fixed_short_windows_are_available_for_comparison(self):
        try:
            cases = list(cases_for(123, ['window_50', 'window_80', 'window_100', 'window_150']))
        except ValueError as exc:
            self.fail(f'Fixed inventory windows are unsupported: {exc}')
        self.assertEqual([case.window_ms for case in cases], [50, 80, 100, 150])
    def setUp(self):
        self.seconds = 0.0
        self.room = InventoryRoom(Scenario(release_after_ms=1000, window_ms=200,
                                           stock=2, competitor_offsets_ms=(80, 200)),
                                  now=lambda: self.seconds)
        self.room.start()

    def test_merchant_releases_independently_of_browser_monitor(self):
        self.assertEqual(self.room.snapshot()['stock'], 0)
        self.seconds = 1.01
        self.assertEqual(self.room.snapshot()['stock'], 2)

    def test_competitor_consumes_inventory_before_our_request(self):
        self.seconds = 1.09
        self.assertEqual(self.room.snapshot()['stock'], 1)
        self.assertTrue(self.room.submit('buyer', 'toy', 20, 1)['success'])
        self.assertEqual(self.room.snapshot()['stock'], 0)

    def test_server_rejects_order_when_client_has_stale_available_state(self):
        self.seconds = 1.21
        self.assertFalse(self.room.submit('buyer', 'toy', 20, 1)['success'])
        self.assertEqual(len(self.room.orders), 0)

    def test_atomic_inventory_prevents_overselling(self):
        self.seconds = 1.09
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda i: self.room.submit(str(i), 'toy', 20, 1), range(8)))
        self.assertEqual(sum(result['success'] for result in results), 1)
        self.assertEqual(len(self.room.orders), 1)

    def test_duplicate_buyer_does_not_create_a_second_order(self):
        self.seconds = 1.01
        self.assertTrue(self.room.submit('buyer', 'toy', 20, 1)['success'])
        self.assertFalse(self.room.submit('buyer', 'toy', 20, 1)['success'])
        self.assertEqual(len(self.room.orders), 1)
        self.assertEqual(self.room.order_attempts, 2)

    def test_server_validates_product_price_and_quantity(self):
        self.seconds = 1.01
        for product, price, quantity in [('other', 20, 1), ('toy', 0, 1), ('toy', 20, 2)]:
            self.assertFalse(self.room.submit('buyer', product, price, quantity)['success'])
        self.assertEqual(self.room.snapshot()['stock'], 2)

    def test_acceptance_timestamp_measures_inventory_release_to_server_order(self):
        self.seconds = 1.05
        self.room.submit('buyer', 'toy', 20, 1)
        self.assertAlmostEqual(self.room.accepted_after_release_ms, 50)


if __name__ == '__main__':
    unittest.main()
