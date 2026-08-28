import unittest

import run_stock_sync_b2b_separate_login as runner


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""

    def json(self):
        return {}


class PlentyCorrectionEnvelopeTests(unittest.TestCase):
    def test_absolute_stock_write_uses_corrections_envelope(self):
        client = object.__new__(runner.b2b.stock.MultiWarehousePlentyClient)
        calls = []

        def fake_request(method, path, *, params=None, json_body=None, is_write=False):
            calls.append((method, path, params, json_body, is_write))
            return FakeResponse(200)

        client.request = fake_request
        client.set_stock_for_warehouse(2184, 283, 2, 1)

        self.assertEqual(len(calls), 1)
        method, path, params, body, is_write = calls[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(path, "/rest/stockmanagement/warehouses/2/stock/correction")
        self.assertIsNone(params)
        self.assertTrue(is_write)
        self.assertEqual(
            body,
            {
                "corrections": [
                    {
                        "variationId": 2184,
                        "reasonId": runner.b2b.stock.sync.STOCK_CORRECTION_REASON_ID,
                        "quantity": 283,
                        "storageLocationId": 1,
                    }
                ]
            },
        )

    def test_global_default_location_zero_is_supported(self):
        client = object.__new__(runner.b2b.stock.MultiWarehousePlentyClient)
        calls = []

        def fake_request(method, path, *, params=None, json_body=None, is_write=False):
            calls.append(json_body)
            return FakeResponse(200)

        client.request = fake_request
        client.set_stock_for_warehouse(2184, 283, 1, 0)
        self.assertEqual(calls[0]["corrections"][0]["storageLocationId"], 0)


if __name__ == "__main__":
    unittest.main()
