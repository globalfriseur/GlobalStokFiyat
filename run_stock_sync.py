from __future__ import annotations

import os
import re
import time
from html import unescape
from typing import Any
from urllib.parse import quote as url_quote, urljoin

import requests

import sync_activeshop_to_plenty as sync


# Stock-only hard safety: this entrypoint can never write prices.
sync.APP_VERSION = 8
sync.UPDATE_PURCHASE_PRICE = False
sync.UPDATE_STOCK = True
sync.UPDATE_SALES_PRICE = False


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if raw.isdigit():
        return int(raw)
    return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else float(default)
    except ValueError:
        return float(default)


_default_active_warehouse = int(sync.PLENTY_WAREHOUSE_ID) if str(sync.PLENTY_WAREHOUSE_ID).isdigit() else 2
_default_active_storage = int(sync.PLENTY_STORAGE_LOCATION_ID) if str(sync.PLENTY_STORAGE_LOCATION_ID).isdigit() else 1

# ActiveShop saleable stock is mirrored to both Plenty warehouses.
ACTIVE_WAREHOUSE_ID = _env_int("PLENTY_ACTIVE_WAREHOUSE_ID", _default_active_warehouse)
GLOBAL_WAREHOUSE_ID = _env_int("PLENTY_GLOBAL_WAREHOUSE_ID", 1)
ACTIVE_STORAGE_LOCATION_ID = _env_int("PLENTY_ACTIVE_STORAGE_LOCATION_ID", _default_active_storage)
# If no dedicated Global Lager location is configured, use the known location ID.
GLOBAL_STORAGE_LOCATION_ID = _env_int("PLENTY_GLOBAL_STORAGE_LOCATION_ID", _default_active_storage)

ACTIVESHOP_FRONTEND_BASE_URL = os.getenv(
    "ACTIVESHOP_FRONTEND_BASE_URL",
    sync.ACTIVESHOP_HOST,
).strip().rstrip("/")
ACTIVESHOP_FRONTEND_TIMEOUT = max(5, _env_int("ACTIVESHOP_FRONTEND_TIMEOUT", 45))
ACTIVESHOP_FRONTEND_REQUEST_SLEEP = max(0.0, _env_float("ACTIVESHOP_FRONTEND_REQUEST_SLEEP", 0.20))

# A product is only called SYNCED after both Plenty warehouses are read back
# and match the ActiveShop target.
STOCK_VERIFY_ATTEMPTS = max(1, _env_int("STOCK_VERIFY_ATTEMPTS", 3))
STOCK_VERIFY_DELAY = max(0.0, _env_float("STOCK_VERIFY_DELAY", 3.0))

# Keep compatibility with the original sync engine. Its stock step is always
# executed in dry-run mode; the real writes and verification happen below.
sync.PLENTY_WAREHOUSE_ID = str(ACTIVE_WAREHOUSE_ID)
sync.PLENTY_STORAGE_LOCATION_ID = str(ACTIVE_STORAGE_LOCATION_ID)

for column in (
    "source_frontend_stock_url",
    "source_frontend_stock_status",
    "plenty_old_stock_global",
    "stock_status_active",
    "stock_status_global",
    "stock_verified_active",
    "stock_verified_global",
    "stock_verify_status",
    "stock_verify_attempts",
):
    if column not in sync.OUTPUT_COLUMNS:
        stock_pos = sync.OUTPUT_COLUMNS.index("stock_status")
        sync.OUTPUT_COLUMNS.insert(stock_pos, column)


def parse_auf_lager_stock(html_text: str) -> float | None:
    """Parse the saleable quantity shown by ActiveShop as `Auf Lager: N Stck.`."""
    if not html_text:
        return None
    cleaned = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", html_text)
    cleaned = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", cleaned)
    cleaned = unescape(re.sub(r"(?s)<[^>]+>", " ", cleaned))
    cleaned = re.sub(r"\s+", " ", cleaned)
    match = re.search(
        r"Auf\s+Lager\s*:\s*([0-9][0-9\s.,]*)\s*St(?:ck|k)\.?\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    if not digits:
        return None
    return float(int(digits))


def _extract_form_key(html_text: str) -> str:
    patterns = (
        r"name=[\"']form_key[\"'][^>]*value=[\"']([^\"']+)[\"']",
        r"value=[\"']([^\"']+)[\"'][^>]*name=[\"']form_key[\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, html_text or "", flags=re.IGNORECASE)
        if match:
            return unescape(match.group(1)).strip()
    return ""


def _extract_product_links(html_text: str, base_url: str) -> list[str]:
    links: list[str] = []
    for anchor in re.findall(r"(?is)<a\b[^>]*>", html_text or ""):
        if "product-item-link" not in anchor.lower():
            continue
        match = re.search(r"href=[\"']([^\"']+)[\"']", anchor, flags=re.IGNORECASE)
        if not match:
            continue
        url = urljoin(base_url + "/", unescape(match.group(1)).strip())
        if url and url not in links:
            links.append(url)
    return links


def _explicit_api_salable_quantity(data: dict[str, Any]) -> float | None:
    ext = data.get("extension_attributes") or {}
    if not isinstance(ext, dict):
        ext = {}
    custom = sync.custom_attributes_to_dict(data)
    for raw in (
        ext.get("salable_quantity"),
        ext.get("salable_qty"),
        data.get("salable_quantity"),
        data.get("salable_qty"),
        custom.get("salablequantity"),
        custom.get("salableqty"),
    ):
        value = sync.to_float(raw)
        if value is not None:
            return max(0.0, value)
    return None


def _frontend_url_candidates(data: dict[str, Any]) -> list[str]:
    custom = sync.custom_attributes_to_dict(data)
    ext = data.get("extension_attributes") or {}
    if not isinstance(ext, dict):
        ext = {}

    values: list[tuple[Any, bool]] = [
        (data.get("product_url"), False),
        (data.get("url"), False),
        (data.get("url_path"), False),
        (data.get("url_key"), True),
        (ext.get("product_url"), False),
        (ext.get("url_path"), False),
        (ext.get("url_key"), True),
        (custom.get("producturl"), False),
        (custom.get("urlpath"), False),
        (custom.get("urlkey"), True),
    ]

    result: list[str] = []
    for raw, is_key in values:
        if raw in (None, "", [], {}):
            continue
        value = str(raw).strip()
        if not value:
            continue
        if value.startswith("http://") or value.startswith("https://"):
            url = value
        else:
            path = value.lstrip("/")
            if is_key and not path.lower().endswith(".html"):
                path += ".html"
            url = urljoin(ACTIVESHOP_FRONTEND_BASE_URL + "/", path)
        if url not in result:
            result.append(url)
    return result


class StockOnlyActiveShopClient(sync.ActiveShopClient):
    """Use the B2B page `Auf Lager` value as the stock synchronization target."""

    def __init__(self) -> None:
        super().__init__()
        self.frontend_stock_meta: dict[str, dict[str, Any]] = {}
        self._frontend_login_attempted = False
        self._frontend_logged_in = False

    @staticmethod
    def _frontend_headers() -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
        }

    def _frontend_get(self, url: str) -> tuple[requests.Response | None, str]:
        try:
            response = self.session.get(
                url,
                headers=self._frontend_headers(),
                timeout=ACTIVESHOP_FRONTEND_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as error:
            return None, str(error)
        if ACTIVESHOP_FRONTEND_REQUEST_SLEEP:
            time.sleep(ACTIVESHOP_FRONTEND_REQUEST_SLEEP)
        if response.status_code != 200:
            return response, f"HTTP {response.status_code}"
        return response, ""

    def _frontend_login(self) -> bool:
        if self._frontend_login_attempted:
            return self._frontend_logged_in
        self._frontend_login_attempted = True

        login_url = f"{ACTIVESHOP_FRONTEND_BASE_URL}/customer/account/login/"
        response, error = self._frontend_get(login_url)
        if response is None or response.status_code != 200:
            self.frontend_stock_meta.setdefault("_login", {})["error"] = error or "login page unavailable"
            return False

        form_key = _extract_form_key(response.text)
        if not form_key:
            try:
                form_key = str(self.session.cookies.get("form_key") or "")
            except Exception:
                form_key = ""

        payload: dict[str, str] = {
            "login[username]": sync.ACTIVESHOP_USERNAME,
            "login[password]": sync.ACTIVESHOP_PASSWORD,
        }
        if form_key:
            payload["form_key"] = form_key

        try:
            posted = self.session.post(
                f"{ACTIVESHOP_FRONTEND_BASE_URL}/customer/account/loginPost/",
                data=payload,
                headers=self._frontend_headers(),
                timeout=ACTIVESHOP_FRONTEND_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as error:
            self.frontend_stock_meta.setdefault("_login", {})["error"] = str(error)
            return False

        final_url = str(posted.url or "").lower()
        self._frontend_logged_in = posted.status_code == 200 and "/customer/account/login" not in final_url
        if not self._frontend_logged_in:
            self.frontend_stock_meta.setdefault("_login", {})["error"] = (
                f"frontend login failed: HTTP {posted.status_code}, url={posted.url}"
            )
        return self._frontend_logged_in

    def _try_urls_for_stock(self, urls: list[str]) -> tuple[float | None, str, int, str]:
        attempts = 0
        errors: list[str] = []
        for url in urls:
            response, error = self._frontend_get(url)
            attempts += 1
            if response is None:
                errors.append(f"{url}: {error}")
                continue
            quantity = parse_auf_lager_stock(response.text) if response.status_code == 200 else None
            if quantity is not None:
                return quantity, str(response.url or url), attempts, ""
            errors.append(f"{url}: {error or 'Auf Lager bulunamadi'}")
        return None, "", attempts, " | ".join(errors)[-2500:]

    def get_frontend_stock(self, sku: str, data: dict[str, Any]) -> dict[str, Any]:
        attempts = 0
        errors: list[str] = []
        candidates = _frontend_url_candidates(data)

        quantity, found_url, used, error = self._try_urls_for_stock(candidates)
        attempts += used
        if quantity is not None:
            return {
                "quantity": quantity,
                "url": found_url,
                "status": "FRONTEND_AUF_LAGER",
                "attempts": attempts,
                "error": "",
            }
        if error:
            errors.append(error)

        # B2B pages may redirect anonymous visitors to Magento customer login.
        if self._frontend_login():
            quantity, found_url, used, error = self._try_urls_for_stock(candidates)
            attempts += used
            if quantity is not None:
                return {
                    "quantity": quantity,
                    "url": found_url,
                    "status": "FRONTEND_AUF_LAGER",
                    "attempts": attempts,
                    "error": "",
                }
            if error:
                errors.append(error)

        # If URL metadata is missing or stale, use Magento catalog search to find
        # the product detail link for the exact SKU and parse that page.
        search_url = (
            f"{ACTIVESHOP_FRONTEND_BASE_URL}/catalogsearch/result/?q="
            f"{url_quote(sku, safe='')}"
        )
        search_response, search_error = self._frontend_get(search_url)
        attempts += 1
        if search_response is not None and search_response.status_code == 200:
            search_links = _extract_product_links(search_response.text, ACTIVESHOP_FRONTEND_BASE_URL)
            quantity, found_url, used, error = self._try_urls_for_stock(search_links[:5])
            attempts += used
            if quantity is not None:
                return {
                    "quantity": quantity,
                    "url": found_url,
                    "status": "FRONTEND_AUF_LAGER_SEARCH",
                    "attempts": attempts,
                    "error": "",
                }
            if error:
                errors.append(error)
        elif search_error:
            errors.append(f"search: {search_error}")

        return {
            "quantity": None,
            "url": "",
            "status": "FRONTEND_STOCK_MISSING",
            "attempts": attempts,
            "error": " | ".join(errors)[-3000:] or "Auf Lager bilgisi bulunamadi",
        }

    def get_product(self, sku: str) -> dict[str, Any]:
        result = super().get_product(sku)
        if result.get("status") != "OK":
            return result

        data = result.get("data")
        if not isinstance(data, dict):
            return result

        frontend = self.get_frontend_stock(sku, data)
        self.frontend_stock_meta[sku] = frontend
        result["attempts"] = int(result.get("attempts", 1) or 1) + int(frontend.get("attempts", 0) or 0)

        quantity = sync.to_float(frontend.get("quantity"))
        if quantity is not None:
            ext = data.get("extension_attributes") or {}
            if not isinstance(ext, dict):
                ext = {}
            else:
                ext = dict(ext)
            # extract_source_values() prefers salable_quantity before stock_qty.
            ext["salable_quantity"] = max(0.0, quantity)
            data["extension_attributes"] = ext
            return result

        # Only an explicitly named salable quantity is a safe fallback. Never
        # silently fall back to stock_qty/qty because that is physical stock and
        # can differ from the B2B page (e.g. 306 physical vs 283 Auf Lager).
        api_salable = _explicit_api_salable_quantity(data)
        if api_salable is not None:
            ext = data.get("extension_attributes") or {}
            if not isinstance(ext, dict):
                ext = {}
            else:
                ext = dict(ext)
            ext["salable_quantity"] = api_salable
            data["extension_attributes"] = ext
            frontend.update({
                "quantity": api_salable,
                "status": "API_EXPLICIT_SALABLE_FALLBACK",
                "error": frontend.get("error", ""),
            })
            self.frontend_stock_meta[sku] = frontend
            return result

        return {
            "status": "FRONTEND_STOCK_MISSING",
            "error": frontend.get("error", "Auf Lager bilgisi bulunamadi"),
            "http_status": result.get("http_status", 200),
            "attempts": result.get("attempts", 1),
        }

    def get_stock_fallback(self, sku: str) -> sync.StockFetchResult:
        # Separate inventory endpoints are unavailable for this customer token,
        # and physical stock_qty must not be used as a saleable-stock fallback.
        return sync.StockFetchResult(
            quantity=None,
            path="",
            status="FRONTEND_STOCK_REQUIRED",
            attempts=0,
            error="B2B sayfasindaki Auf Lager stogu zorunlu",
        )


class MultiWarehousePlentyClient(sync.PlentyClient):
    """Read current stock and set absolute stock through StockManagement routes."""

    def list_variation_stock(self, item_id: int, variation_id: int) -> list[dict[str, Any]]:
        response = self.request(
            "GET",
            "/rest/stockmanagement/stock",
            params={"variationId": int(variation_id), "itemsPerPage": 100},
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Plenty mevcut stok okunamadi: HTTP {response.status_code} - {sync.response_error_text(response)}"
            )
        payload = response.json()
        if isinstance(payload, list):
            return [entry for entry in payload if isinstance(entry, dict)]
        if isinstance(payload, dict):
            entries = payload.get("entries") or payload.get("data") or []
            if isinstance(entries, list):
                return [entry for entry in entries if isinstance(entry, dict)]
        return []

    def set_stock_for_warehouse(
        self,
        variation_id: int,
        target_quantity: float,
        warehouse_id: int,
        storage_location_id: int = 0,
    ) -> None:
        payload: dict[str, Any] = {
            "variationId": int(variation_id),
            "quantity": float(target_quantity),
            "reasonId": sync.STOCK_CORRECTION_REASON_ID,
        }
        if int(storage_location_id) > 0:
            payload["storageLocationId"] = int(storage_location_id)

        response = self.request(
            "PUT",
            f"/rest/stockmanagement/warehouses/{int(warehouse_id)}/stock/correction",
            json_body=payload,
            is_write=True,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(
                "Plenty stok guncellenemedi "
                f"(warehouse={warehouse_id}, target={target_quantity}): "
                f"HTTP {response.status_code} - {sync.response_error_text(response)}"
            )


# main() instantiates these classes through the original sync module.
sync.ActiveShopClient = StockOnlyActiveShopClient
sync.PlentyClient = MultiWarehousePlentyClient


def stock_snapshot(entries: list[dict[str, Any]], warehouse_id: int) -> dict[str, float | None]:
    physical: list[float] = []
    net: list[float] = []
    for entry in entries:
        wid = entry.get("warehouseId") or entry.get("warehouse_id") or entry.get("warehouse")
        if isinstance(wid, dict):
            wid = wid.get("id")
        if sync.clean_identifier(wid) != str(warehouse_id):
            continue

        for key in ("stockPhysical", "physicalStock", "stock_physical", "physical_stock", "stock"):
            value = sync.to_float(entry.get(key))
            if value is not None:
                physical.append(value)
                break

        for key in ("stockNet", "netStock", "stock_net", "net_stock", "net"):
            value = sync.to_float(entry.get(key))
            if value is not None:
                net.append(value)
                break

    return {
        "physical": sum(physical) if physical else 0.0,
        "net": sum(net) if net else None,
    }


def _append_error(row: dict[str, Any], message: str) -> None:
    row["error"] = (f"{row.get('error', '')} | {message}").strip(" |")[:4000]


def _attach_frontend_meta(row: dict[str, Any], active: Any, sku: str) -> None:
    meta = getattr(active, "frontend_stock_meta", {}).get(sku, {})
    if not isinstance(meta, dict):
        return
    row["source_frontend_stock_url"] = meta.get("url", "")
    row["source_frontend_stock_status"] = meta.get("status", "")
    quantity = sync.to_float(meta.get("quantity"))
    if quantity is not None and str(meta.get("status", "")).startswith("FRONTEND_AUF_LAGER"):
        row["source_stock"] = quantity
        row["source_stock_path"] = "frontend.Auf_Lager"


def _read_two_warehouse_stocks(
    plenty: MultiWarehousePlentyClient,
    item_id: int,
    variation_id: int,
) -> tuple[float, float]:
    entries = plenty.list_variation_stock(item_id, variation_id)
    active_stock = float(stock_snapshot(entries, ACTIVE_WAREHOUSE_ID)["physical"] or 0.0)
    global_stock = float(stock_snapshot(entries, GLOBAL_WAREHOUSE_ID)["physical"] or 0.0)
    return active_stock, global_stock


def _same_stock(actual: float, target: float) -> bool:
    return abs(float(actual) - float(target)) < 0.0001


_original_process_one = sync.process_one


def direct_process_one(source, previous, cycle, active, plenty):
    """Use frontend saleable stock, write it, then strictly verify both warehouses."""
    sku = source.get("input_sku", "")
    write_enabled = bool(sync.PLENTY_ENABLE_WRITE)
    try:
        # Never allow the legacy item-level correction path to perform a write.
        sync.PLENTY_ENABLE_WRITE = False
        row, stop = _original_process_one(source, previous, cycle, active, plenty)
    finally:
        sync.PLENTY_ENABLE_WRITE = write_enabled

    _attach_frontend_meta(row, active, sku)

    if stop or not sync.UPDATE_STOCK:
        return row, stop

    item_id = sync.to_float(row.get("plenty_item_id"))
    variation_id = sync.to_float(row.get("plenty_variation_id"))
    target_stock = sync.to_float(row.get("plenty_target_stock"))
    if item_id is None or variation_id is None or target_stock is None:
        return row, stop

    item_id_int = int(item_id)
    variation_id_int = int(variation_id)
    target = float(target_stock)

    sync.log(
        f"STOK KAYNAK | {sku} | ActiveShop Auf Lager={target:g} | "
        f"kaynak={row.get('source_frontend_stock_status', '')} | "
        f"url={row.get('source_frontend_stock_url', '')}"
    )

    try:
        active_old, global_old = _read_two_warehouse_stocks(plenty, item_id_int, variation_id_int)
    except Exception as error:
        row["stock_status_active"] = "ERROR"
        row["stock_status_global"] = "ERROR"
        row["stock_status"] = "ERROR"
        row["stock_verify_status"] = "READ_ERROR"
        row["overall_status"] = "PARTIAL_ERROR"
        _append_error(row, f"Plenty mevcut stok okuma hatasi: {error}")
        return row, stop

    row["plenty_old_stock"] = active_old
    row["plenty_old_stock_global"] = global_old
    row["stock_delta"] = round(target - active_old, 4)
    row["stock_verified_active"] = active_old
    row["stock_verified_global"] = global_old
    row["stock_verify_attempts"] = 0

    active_needs_write = not _same_stock(active_old, target)
    global_needs_write = not _same_stock(global_old, target)

    # The initial read itself is a valid verification when both already match.
    if not active_needs_write and not global_needs_write:
        row["stock_status_active"] = "VERIFIED"
        row["stock_status_global"] = "VERIFIED"
        row["stock_status"] = "NO_CHANGE"
        row["stock_verify_status"] = "VERIFIED_NO_CHANGE"
        row["overall_status"] = "NO_CHANGE"
        sync.log(
            f"STOK DOGRULAMA | {sku} | hedef={target:g} | Active={active_old:g} | "
            f"Global={global_old:g} | durum=VERIFIED_NO_CHANGE"
        )
        return row, stop

    if not write_enabled:
        row["stock_status_active"] = "DRY_RUN_CORRECTION" if active_needs_write else "VERIFIED"
        row["stock_status_global"] = "DRY_RUN_CORRECTION" if global_needs_write else "VERIFIED"
        row["stock_status"] = "DRY_RUN_CORRECTION"
        row["stock_verify_status"] = "DRY_RUN_NOT_VERIFIED"
        row["overall_status"] = "DRY_RUN_OK"
        return row, stop

    current_active = active_old
    current_global = global_old
    write_errors: list[str] = []
    last_read_error = ""

    for attempt in range(1, STOCK_VERIFY_ATTEMPTS + 1):
        if not _same_stock(current_active, target):
            try:
                plenty.set_stock_for_warehouse(
                    variation_id_int,
                    target,
                    ACTIVE_WAREHOUSE_ID,
                    ACTIVE_STORAGE_LOCATION_ID,
                )
            except Exception as error:
                write_errors.append(f"Active Shop deneme {attempt}: {error}")

        if not _same_stock(current_global, target):
            try:
                plenty.set_stock_for_warehouse(
                    variation_id_int,
                    target,
                    GLOBAL_WAREHOUSE_ID,
                    GLOBAL_STORAGE_LOCATION_ID,
                )
            except Exception as error:
                write_errors.append(f"Global Lager deneme {attempt}: {error}")

        if STOCK_VERIFY_DELAY:
            time.sleep(STOCK_VERIFY_DELAY)

        try:
            current_active, current_global = _read_two_warehouse_stocks(
                plenty,
                item_id_int,
                variation_id_int,
            )
            last_read_error = ""
        except Exception as error:
            last_read_error = str(error)
            row["stock_verify_attempts"] = attempt
            if attempt < STOCK_VERIFY_ATTEMPTS:
                continue
            break

        row["stock_verified_active"] = current_active
        row["stock_verified_global"] = current_global
        row["stock_verify_attempts"] = attempt

        sync.log(
            f"STOK DOGRULAMA | {sku} | hedef={target:g} | Active={current_active:g} | "
            f"Global={current_global:g} | deneme={attempt}/{STOCK_VERIFY_ATTEMPTS}"
        )

        if _same_stock(current_active, target) and _same_stock(current_global, target):
            row["stock_status_active"] = "VERIFIED"
            row["stock_status_global"] = "VERIFIED"
            row["stock_status"] = "VERIFIED"
            row["stock_verify_status"] = "VERIFIED"
            row["overall_status"] = "SYNCED"
            row["synced_at_utc"] = sync.utc_iso()
            return row, stop

    row["stock_status_active"] = "VERIFIED" if _same_stock(current_active, target) else "MISMATCH"
    row["stock_status_global"] = "VERIFIED" if _same_stock(current_global, target) else "MISMATCH"
    row["stock_status"] = "MISMATCH"
    row["stock_verify_status"] = "MISMATCH"
    row["overall_status"] = "STOCK_MISMATCH"

    details = (
        f"Stok eslesmedi: hedef={target:g}, Active Shop={current_active:g}, "
        f"Global Lager={current_global:g}, deneme={row.get('stock_verify_attempts', 0)}"
    )
    if last_read_error:
        details += f" | son okuma hatasi: {last_read_error}"
    if write_errors:
        details += " | yazma hatalari: " + " ; ".join(write_errors[-4:])
    _append_error(row, details)
    return row, stop


sync.process_one = direct_process_one


if __name__ == "__main__":
    raise SystemExit(sync.main())
