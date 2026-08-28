import unittest

import run_stock_sync_b2b as b2b


class B2BRouteTests(unittest.TestCase):
    def test_url_key_prefers_de_b2b_route(self):
        data = {
            "custom_attributes": [
                {"attribute_code": "url_key", "value": "nghia-export-pinzette-t-04"}
            ]
        }
        urls = b2b.frontend_url_candidates(data)
        expected = "https://b2b.activeshop.com.pl/de/nghia-export-pinzette-t-04.html"
        self.assertIn(expected, urls)
        self.assertEqual(urls[0], expected)

    def test_b2b_candidates_keep_authenticated_route(self):
        data = {
            "custom_attributes": [
                {"attribute_code": "url_key", "value": "nghia-export-pinzette-t-04"}
            ]
        }
        urls = b2b._b2b_candidates(data)
        self.assertTrue(urls)
        self.assertTrue(all(url.startswith("https://b2b.activeshop.com.pl/de/") for url in urls))

    def test_systematic_frontend_stock_error_stops_without_skipping_sku(self):
        original = b2b._base_stock_process_one
        try:
            b2b._base_stock_process_one = lambda *args, **kwargs: (
                {
                    "overall_status": "SOURCE_ERROR",
                    "source_status": "FRONTEND_STOCK_MISSING",
                    "error": "Auf Lager bulunamadi",
                },
                False,
            )
            row, stop = b2b.guarded_process_one(
                {"input_sku": "122780"}, {}, 2, object(), object()
            )
            self.assertTrue(stop)
            self.assertEqual(row["source_status"], "FRONTEND_STOCK_MISSING")
        finally:
            b2b._base_stock_process_one = original

    def test_non_frontend_error_does_not_force_stop(self):
        original = b2b._base_stock_process_one
        try:
            b2b._base_stock_process_one = lambda *args, **kwargs: (
                {
                    "overall_status": "PLENTY_MATCH_ERROR",
                    "source_status": "OK",
                },
                False,
            )
            _, stop = b2b.guarded_process_one(
                {"input_sku": "122780"}, {}, 2, object(), object()
            )
            self.assertFalse(stop)
        finally:
            b2b._base_stock_process_one = original


if __name__ == "__main__":
    unittest.main()
