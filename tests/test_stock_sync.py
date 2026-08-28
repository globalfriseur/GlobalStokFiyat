import unittest

import run_stock_sync as stock


USER_HTML = """
<div class="border-t grow py-4 px-2 text-right">
    <p class="flex items-center justify-end align-middle gap-x-2">
        <span class="capitalize">Auf Lager:</span>
        <span class="text-gray-700 font-semibold lowercase">283 Stck.</span>
    </p>
</div>
"""


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", url="https://b2b.activeshop.com.pl/test.html"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.url = url

    def json(self):
        return self._payload


class StockSyncTests(unittest.TestCase):
    def test_price_writes_are_hard_disabled(self):
        self.assertEqual(stock.sync.APP_VERSION, 8)
        self.assertFalse(stock.sync.UPDATE_PURCHASE_PRICE)
        self.assertTrue(stock.sync.UPDATE_STOCK)
        self.assertFalse(stock.sync.UPDATE_SALES_PRICE)

    def test_parse_exact_user_html_auf_lager_283(self):
        self.assertEqual(stock.parse_auf_lager_stock(USER_HTML), 283.0)

    def test_parse_auf_lager_zero(self):
        html = '<span>Auf Lager:</span><span>0 Stck.</span>'
        self.assertEqual(stock.parse_auf_lager_stock(html), 0.0)

    def test_frontend_url_uses_magento_url_key(self):
        data = {
            "custom_attributes": [
                {"attribute_code": "url_key", "value": "nghia-export-pinzette-t-04"}
            ]
        }
        urls = stock._frontend_url_candidates(data)
        self.assertIn(
            f"{stock.ACTIVESHOP_FRONTEND_BASE_URL}/nghia-export-pinzette-t-04.html",
            urls,
        )

    def test_frontend_stock_overrides_physical_stock_qty(self):
        client = stock.StockOnlyActiveShopClient()

        def fake_get_json(path, *, sku, params=None, not_found_status="NOT_FOUND"):
            return {
                "status": "OK",
                "error": "",
                "http_status": 200,
                "attempts": 1,
                "data": {
                    "sku": sku,
                    "price": 2.86,
                    "extension_attributes": {"stock_qty": 306},
                },
            }

        client._get_json = fake_get_json
        client.get_frontend_stock = lambda sku, data: {
            "quantity": 283,
            "url": "https://b2b.activeshop.com.pl/nghia-export-pinzette-t-04.html",
            "status": "FRONTEND_AUF_LAGER",
            "attempts": 1,
            "error": "",
        }

        result = client.get_product("122780")
        self.assertEqual(result["status"], "OK")
        values = stock.sync.extract_source_values(result["data"], "122780", "")
        self.assertEqual(values.stock, 283)
        self.assertEqual(values.stock_path, "extension_attributes.salable_quantity")
        self.assertNotEqual(values.stock, 306)

    def test_physical_stock_qty_is_not_used_when_frontend_missing(self):
        client = stock.StockOnlyActiveShopClient()

        def fake_get_json(path, *, sku, params=None, not_found_status="NOT_FOUND"):
            return {
                "status": "OK",
                "error": "",
                "http_status": 200,
                "attempts": 1,
                "data": {
                    "sku": sku,
                    "extension_attributes": {"stock_qty": 306},
                },
            }

        client._get_json = fake_get_json
        client.get_frontend_stock = lambda sku, data: {
            "quantity": None,
            "url": "",
            "status": "FRONTEND_STOCK_MISSING",
            "attempts": 1,
            "error": "Auf Lager bulunamadi",
        }

        result = client.get_product("122780")
        self.assertEqual(result["status"], "FRONTEND_STOCK_MISSING")

    def test_explicit_api_salable_is_safe_fallback(self):
        client = stock.StockOnlyActiveShopClient()

        def fake_get_json(path, *, sku, params=None, not_found_status="NOT_FOUND"):
            return {
                "status": "OK",
                "error": "",
                "http_status": 200,
                "attempts": 1,
                "data": {
                    "sku": sku,
                    "extension_attributes": {
                        "stock_qty": 306,
                        "salable_quantity": 283,
                    },
                },
            }

        client._get_json = fake_get_json
        client.get_frontend_stock = lambda sku, data: {
            "quantity": None,
            "url": "",
            "status": "FRONTEND_STOCK_MISSING",
            "attempts": 1,
            "error": "frontend unavailable",
        }

        result = client.get_product("122780")
        self.assertEqual(result["status"], "OK")
        values = stock.sync.extract_source_values(result["data"], "122780", "")
        self.assertEqual(values.stock, 283)
        self.assertEqual(client.frontend_stock_meta["122780"]["status"], "API_EXPLICIT_SALABLE_FALLBACK")

    def test_stock_snapshot_uses_physical_stock(self):
        entries = [
            {"warehouseId": 1, "stockPhysical": 54, "stockNet": 52},
            {"warehouseId": 2, "stockPhysical": 12, "stockNet": 10},
        ]
        result = stock.stock_snapshot(entries, 1)
        self.assertEqual(result["physical"], 54)
        self.assertEqual(result["net"], 52)

    def test_absolute_write_payload_uses_target(self):
        client = object.__new__(stock.MultiWarehousePlentyClient)
        calls = []

        def fake_request(method, path, *, params=None, json_body=None, is_write=False):
            calls.append((method, path, params, json_body, is_write))
            return FakeResponse(200, {})

        client.request = fake_request
        client.set_stock_for_warehouse(2184, 283, 2, 1)

        method, path, params, body, is_write = calls[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(path, "/rest/stockmanagement/warehouses/2/stock/correction")
        self.assertIsNone(params)
        self.assertTrue(is_write)
        self.assertEqual(body["variationId"], 2184)
        self.assertEqual(body["quantity"], 283.0)
        self.assertEqual(body["storageLocationId"], 1)

    def _base_original_row(self, target=283):
        return {
            "input_sku": "122780",
            "plenty_item_id": 1020,
            "plenty_variation_id": 2184,
            "plenty_target_stock": target,
            "source_stock": target,
            "stock_status": "DRY_RUN_CORRECTION",
            "overall_status": "DRY_RUN_OK",
            "purchase_price_status": "DISABLED",
            "error": "",
        }

    def test_synced_only_after_both_warehouses_verify(self):
        class FakePlenty:
            def __init__(self):
                self.state = {1: 0.0, 2: 306.0}
                self.reads = 0
                self.writes = []

            def list_variation_stock(self, item_id, variation_id):
                self.reads += 1
                return [
                    {"warehouseId": 1, "stockPhysical": self.state[1]},
                    {"warehouseId": 2, "stockPhysical": self.state[2]},
                ]

            def set_stock_for_warehouse(self, variation_id, target, warehouse_id, storage_location_id=0):
                self.writes.append((variation_id, target, warehouse_id, storage_location_id))
                self.state[warehouse_id] = float(target)

        original_process = stock._original_process_one
        original_write = stock.sync.PLENTY_ENABLE_WRITE
        original_delay = stock.STOCK_VERIFY_DELAY
        try:
            stock._original_process_one = lambda *args, **kwargs: (self._base_original_row(), False)
            stock.sync.PLENTY_ENABLE_WRITE = True
            stock.STOCK_VERIFY_DELAY = 0
            active = type("Active", (), {"frontend_stock_meta": {
                "122780": {
                    "quantity": 283,
                    "url": "https://b2b.activeshop.com.pl/test.html",
                    "status": "FRONTEND_AUF_LAGER",
                }
            }})()
            plenty = FakePlenty()

            row, stop = stock.direct_process_one(
                {"input_sku": "122780"}, {}, 2, active, plenty
            )

            self.assertFalse(stop)
            self.assertEqual(row["overall_status"], "SYNCED")
            self.assertEqual(row["stock_status"], "VERIFIED")
            self.assertEqual(row["stock_verify_status"], "VERIFIED")
            self.assertEqual(row["stock_verified_active"], 283)
            self.assertEqual(row["stock_verified_global"], 283)
            self.assertEqual(row["stock_verify_attempts"], 1)
            self.assertEqual(plenty.state, {1: 283.0, 2: 283.0})
            self.assertEqual(len(plenty.writes), 2)
            self.assertGreaterEqual(plenty.reads, 2)
        finally:
            stock._original_process_one = original_process
            stock.sync.PLENTY_ENABLE_WRITE = original_write
            stock.STOCK_VERIFY_DELAY = original_delay

    def test_persistent_mismatch_is_never_synced(self):
        class FakePlenty:
            def __init__(self):
                self.reads = 0
                self.writes = 0

            def list_variation_stock(self, item_id, variation_id):
                self.reads += 1
                return [
                    {"warehouseId": 1, "stockPhysical": 0},
                    {"warehouseId": 2, "stockPhysical": 306},
                ]

            def set_stock_for_warehouse(self, variation_id, target, warehouse_id, storage_location_id=0):
                self.writes += 1
                # Simulate an API call that returns success but does not change stock.

        original_process = stock._original_process_one
        original_write = stock.sync.PLENTY_ENABLE_WRITE
        original_delay = stock.STOCK_VERIFY_DELAY
        original_attempts = stock.STOCK_VERIFY_ATTEMPTS
        try:
            stock._original_process_one = lambda *args, **kwargs: (self._base_original_row(), False)
            stock.sync.PLENTY_ENABLE_WRITE = True
            stock.STOCK_VERIFY_DELAY = 0
            stock.STOCK_VERIFY_ATTEMPTS = 3
            plenty = FakePlenty()

            row, _ = stock.direct_process_one(
                {"input_sku": "122780"}, {}, 2, object(), plenty
            )

            self.assertEqual(row["overall_status"], "STOCK_MISMATCH")
            self.assertEqual(row["stock_status"], "MISMATCH")
            self.assertEqual(row["stock_status_active"], "MISMATCH")
            self.assertEqual(row["stock_status_global"], "MISMATCH")
            self.assertNotEqual(row["overall_status"], "SYNCED")
            self.assertEqual(row["stock_verify_attempts"], 3)
            self.assertIn("hedef=283", row["error"])
            self.assertEqual(plenty.writes, 6)
        finally:
            stock._original_process_one = original_process
            stock.sync.PLENTY_ENABLE_WRITE = original_write
            stock.STOCK_VERIFY_DELAY = original_delay
            stock.STOCK_VERIFY_ATTEMPTS = original_attempts

    def test_no_change_is_already_verified(self):
        class FakePlenty:
            def __init__(self):
                self.writes = 0

            def list_variation_stock(self, item_id, variation_id):
                return [
                    {"warehouseId": 1, "stockPhysical": 283},
                    {"warehouseId": 2, "stockPhysical": 283},
                ]

            def set_stock_for_warehouse(self, *args, **kwargs):
                self.writes += 1

        original_process = stock._original_process_one
        original_write = stock.sync.PLENTY_ENABLE_WRITE
        try:
            stock._original_process_one = lambda *args, **kwargs: (self._base_original_row(), False)
            stock.sync.PLENTY_ENABLE_WRITE = True
            plenty = FakePlenty()

            row, _ = stock.direct_process_one(
                {"input_sku": "122780"}, {}, 2, object(), plenty
            )

            self.assertEqual(row["overall_status"], "NO_CHANGE")
            self.assertEqual(row["stock_verify_status"], "VERIFIED_NO_CHANGE")
            self.assertEqual(plenty.writes, 0)
        finally:
            stock._original_process_one = original_process
            stock.sync.PLENTY_ENABLE_WRITE = original_write


if __name__ == "__main__":
    unittest.main()
