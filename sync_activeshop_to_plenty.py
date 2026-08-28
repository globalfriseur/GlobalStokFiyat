from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


APP_VERSION = 7


# ==========================================================
# CONFIGURATION
# ==========================================================

# ActiveShop source
ACTIVESHOP_HOST = os.getenv("ACTIVESHOP_HOST", "https://b2b.activeshop.com.pl").rstrip("/")
ACTIVESHOP_STORE_CODE = os.getenv("ACTIVESHOP_STORE_CODE", "B2B_PL_de").strip()
ACTIVESHOP_USERNAME = os.getenv("ACTIVESHOP_USERNAME", "").strip()
ACTIVESHOP_PASSWORD = os.getenv("ACTIVESHOP_PASSWORD", "").strip()
ACTIVESHOP_PROXY_URL = os.getenv("ACTIVESHOP_PROXY_URL", "").strip()
ACTIVESHOP_STOCK_FALLBACK = os.getenv("ACTIVESHOP_STOCK_FALLBACK", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
ACTIVESHOP_STOCK_ID = os.getenv("ACTIVESHOP_STOCK_ID", "1").strip()
ACTIVESHOP_SOURCE_CODES = {
    value.strip()
    for value in os.getenv("ACTIVESHOP_SOURCE_CODES", "").split(",")
    if value.strip()
}
ACTIVESHOP_STOCK_ENDPOINT_ORDER = [
    value.strip().lower()
    for value in os.getenv(
        "ACTIVESHOP_STOCK_ENDPOINT_ORDER",
        "salable,legacy,source_items",
    ).split(",")
    if value.strip()
]

# Plenty target
PLENTY_BASE_URL = os.getenv("PLENTY_BASE_URL", "").strip().rstrip("/")
PLENTY_USERNAME = os.getenv("PLENTY_USERNAME", "").strip()
PLENTY_PASSWORD = os.getenv("PLENTY_PASSWORD", "").strip()
PLENTY_WAREHOUSE_ID = os.getenv("PLENTY_WAREHOUSE_ID", "").strip()
PLENTY_STORAGE_LOCATION_ID = os.getenv("PLENTY_STORAGE_LOCATION_ID", "").strip()
PLENTY_SALES_PRICE_ID = os.getenv("PLENTY_SALES_PRICE_ID", "").strip()

PLENTY_ENABLE_WRITE = os.getenv("PLENTY_ENABLE_WRITE", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
UPDATE_PURCHASE_PRICE = os.getenv("UPDATE_PURCHASE_PRICE", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
UPDATE_STOCK = os.getenv("UPDATE_STOCK", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
UPDATE_SALES_PRICE = os.getenv("UPDATE_SALES_PRICE", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

# Supplier price -> Plenty purchase price. This is intentionally 1.0 by default.
PURCHASE_PRICE_MULTIPLIER = float(os.getenv("PURCHASE_PRICE_MULTIPLIER", "1.0"))
PURCHASE_PRICE_ADD = float(os.getenv("PURCHASE_PRICE_ADD", "0.0"))

# Optional sales-price formula. Disabled by default to avoid selling at supplier cost.
SALES_PRICE_MULTIPLIER = float(os.getenv("SALES_PRICE_MULTIPLIER", "1.0"))
SALES_PRICE_ADD = float(os.getenv("SALES_PRICE_ADD", "0.0"))

# Stock behavior
STOCK_CORRECTION_REASON_ID = int(os.getenv("STOCK_CORRECTION_REASON_ID", "301"))
STOCK_SAFETY_DEDUCTION = float(os.getenv("STOCK_SAFETY_DEDUCTION", "0"))
STOCK_MAXIMUM = float(os.getenv("STOCK_MAXIMUM", "999999"))

# Files and batching
INPUT_CSV = Path(os.getenv("INPUT_CSV", "input/Global Fiyat Guncelleme.csv"))
OUTPUT_CSV = Path(os.getenv("OUTPUT_CSV", "output/activeshop_plenty_sync.csv"))
FAILED_CSV = Path(os.getenv("FAILED_CSV", "output/failed_products.csv"))
SUMMARY_JSON = Path(os.getenv("SUMMARY_JSON", "output/run_summary.json"))
STATE_JSON = Path(os.getenv("STATE_JSON", "state/plenty_sync_progress.json"))

SKU_COLUMN = os.getenv("SKU_COLUMN", "sku")
EAN_COLUMN = os.getenv("EAN_COLUMN", "EAN")
NAME_COLUMN = os.getenv("NAME_COLUMN", "name")
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "EUR")

MAX_PRODUCTS_PER_RUN = max(1, int(os.getenv("MAX_PRODUCTS_PER_RUN", "300")))
MAX_RUN_MINUTES = max(5, int(os.getenv("MAX_RUN_MINUTES", "100")))
CHECKPOINT_EVERY = max(1, int(os.getenv("CHECKPOINT_EVERY", "25")))

ACTIVESHOP_REQUEST_SLEEP = max(0.0, float(os.getenv("ACTIVESHOP_REQUEST_SLEEP", "0.90")))
ACTIVESHOP_REQUEST_JITTER = max(0.0, float(os.getenv("ACTIVESHOP_REQUEST_JITTER", "0.25")))
ACTIVESHOP_TIMEOUT = max(5, int(os.getenv("ACTIVESHOP_TIMEOUT", "60")))
ACTIVESHOP_RATE_RETRIES = max(0, int(os.getenv("ACTIVESHOP_RATE_RETRIES", "4")))
ACTIVESHOP_RATE_BASE_WAIT = max(1.0, float(os.getenv("ACTIVESHOP_RATE_BASE_WAIT", "20")))
ACTIVESHOP_RATE_MAX_WAIT = max(1.0, float(os.getenv("ACTIVESHOP_RATE_MAX_WAIT", "180")))

PLENTY_TIMEOUT = max(5, int(os.getenv("PLENTY_TIMEOUT", "60")))
PLENTY_READ_SLEEP = max(0.0, float(os.getenv("PLENTY_READ_SLEEP", "0.10")))
# Basic plans can have strict write-call limits. Keep a conservative interval by default.
PLENTY_WRITE_INTERVAL = max(0.0, float(os.getenv("PLENTY_WRITE_INTERVAL", "1.70")))
PLENTY_MAX_RETRIES = max(0, int(os.getenv("PLENTY_MAX_RETRIES", "5")))

GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY", "").strip()

OUTPUT_COLUMNS = [
    "input_sku", "input_ean", "input_name",
    "source_api_sku", "source_api_ean", "source_api_name",
    "source_currency", "source_price_diamond", "source_price_path",
    "source_stock", "source_stock_path", "source_stock_status",
    "plenty_item_id", "plenty_variation_id", "plenty_variation_number", "plenty_match_method",
    "plenty_old_purchase_price", "plenty_target_purchase_price", "purchase_price_status",
    "plenty_old_stock", "plenty_target_stock", "stock_delta", "stock_status",
    "plenty_sales_price_id", "plenty_target_sales_price", "sales_price_status",
    "source_status", "plenty_status", "overall_status", "error",
    "fetched_at_utc", "synced_at_utc", "cycle", "attempts_total",
]


# ==========================================================
# HELPERS
# ==========================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return utc_now().isoformat()


def log(message: str) -> None:
    stamp = utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{stamp}] {message}", flush=True)


def clean_identifier(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def normalize_key(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def round_price(value: float) -> float:
    return round(float(value) + 1e-10, 4)


def custom_attributes_to_dict(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    attrs = data.get("custom_attributes") or []
    if isinstance(attrs, list):
        for item in attrs:
            if not isinstance(item, dict):
                continue
            key = item.get("attribute_code") or item.get("code")
            if key:
                result[normalize_key(key)] = item.get("value", "")
    return result


def response_error_text(response: requests.Response, max_length: int = 1500) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            value = payload.get("message") or payload.get("error") or payload
            return str(value)[:max_length]
        return str(payload)[:max_length]
    except ValueError:
        return response.text[:max_length]


def is_activeshop_limit(response: requests.Response) -> bool:
    if response.status_code not in {400, 429}:
        return False
    text = response_error_text(response, 2500).lower()
    markers = (
        "maximale anzahl von anfragen",
        "maximum number of requests",
        "too many requests",
        "rate limit",
    )
    return any(marker in text for marker in markers)


def retry_after_seconds(response: requests.Response, fallback: float) -> float:
    raw = response.headers.get("Retry-After", "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return fallback


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def atomic_write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[columns]
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False, encoding="utf-8-sig")
    temp.replace(path)


def append_step_summary(lines: list[str]) -> None:
    if not GITHUB_STEP_SUMMARY:
        return
    try:
        with open(GITHUB_STEP_SUMMARY, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as error:
        log(f"GitHub step summary yazilamadi: {error}")


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=1,
        status_forcelist=(),
        allowed_methods=False,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


# ==========================================================
# SOURCE EXTRACTION
# ==========================================================


@dataclass
class SourceValues:
    price: float | None
    price_path: str
    currency: str
    stock: float | None
    stock_path: str
    api_sku: str
    api_ean: str
    api_name: str


@dataclass
class StockFetchResult:
    quantity: float | None
    path: str
    status: str
    attempts: int = 0
    error: str = ""


def extract_stock_from_payload(data: Any, prefix: str = "") -> tuple[float | None, str]:
    """Extract stock from common Magento/Adobe Commerce response shapes."""
    if isinstance(data, (int, float, str)):
        value = to_float(data)
        return max(0.0, value), prefix.rstrip(".") if value is not None else ""
    if not isinstance(data, dict):
        return None, ""

    ext = data.get("extension_attributes") or {}
    if not isinstance(ext, dict):
        ext = {}
    stock_item = ext.get("stock_item") or data.get("stock_item") or {}
    if not isinstance(stock_item, dict):
        stock_item = {}
    custom = custom_attributes_to_dict(data)

    candidates = [
        ("extension_attributes.salable_quantity", ext.get("salable_quantity")),
        ("extension_attributes.salable_qty", ext.get("salable_qty")),
        ("extension_attributes.stock_qty", ext.get("stock_qty")),
        ("extension_attributes.quantity", ext.get("quantity")),
        ("extension_attributes.qty", ext.get("qty")),
        ("extension_attributes.stock", ext.get("stock")),
        ("stock_item.qty", stock_item.get("qty")),
        ("stock_item.quantity", stock_item.get("quantity")),
        ("salable_quantity", data.get("salable_quantity")),
        ("salable_qty", data.get("salable_qty")),
        ("stock_qty", data.get("stock_qty")),
        ("quantity", data.get("quantity")),
        ("qty", data.get("qty")),
        ("stock", data.get("stock")),
        ("custom_attributes.stock_qty", custom.get("stockqty")),
        ("custom_attributes.qty", custom.get("qty")),
    ]
    for path, raw in candidates:
        value = to_float(raw)
        if value is not None:
            return max(0.0, value), f"{prefix}{path}"

    stock_flags = [
        ext.get("is_salable"),
        ext.get("is_in_stock"),
        stock_item.get("is_in_stock"),
        data.get("is_salable"),
        data.get("is_in_stock"),
        data.get("status"),
    ]
    for raw in stock_flags:
        if raw is False or raw == 0 or str(raw).strip().lower() in {"false", "0", "out_of_stock"}:
            return 0.0, f"{prefix}stock_flag"
    return None, ""


def extract_source_values(data: dict[str, Any], input_sku: str, input_ean: str) -> SourceValues:
    ext = data.get("extension_attributes") or {}
    if not isinstance(ext, dict):
        ext = {}
    custom = custom_attributes_to_dict(data)

    candidates = [
        ("extension_attributes.final_price", ext.get("final_price")),
        ("extension_attributes.price_diamond", ext.get("price_diamond")),
        ("extension_attributes.diamond_price", ext.get("diamond_price")),
        ("final_price", data.get("final_price")),
        ("price_diamond", data.get("price_diamond")),
        ("diamond_price", data.get("diamond_price")),
        ("custom_attributes.final_price", custom.get("finalprice")),
        ("custom_attributes.price_diamond", custom.get("pricediamond")),
        ("extension_attributes.group_price", ext.get("group_price")),
        ("price", data.get("price")),
    ]
    price: float | None = None
    price_path = ""
    for path, raw in candidates:
        value = to_float(raw)
        if value is not None:
            price = value
            price_path = path
            break

    stock, stock_path = extract_stock_from_payload(data)

    ean = input_ean
    for key in ("ean", "ean13", "barcode", "gtin"):
        direct = clean_identifier(data.get(key))
        if direct:
            ean = direct
            break
        indirect = clean_identifier(ext.get(key))
        if indirect:
            ean = indirect
            break

    currency = str(first_non_empty(
        ext.get("currency"), ext.get("currency_code"), data.get("currency"),
        data.get("currency_code"), custom.get("currency"), DEFAULT_CURRENCY,
    ))

    return SourceValues(
        price=price,
        price_path=price_path,
        currency=currency,
        stock=stock,
        stock_path=stock_path,
        api_sku=clean_identifier(data.get("sku", input_sku)),
        api_ean=ean,
        api_name=str(data.get("name", "")),
    )


# ==========================================================
# INPUT / STATE / OUTPUT
# ==========================================================


def read_input_rows() -> list[dict[str, str]]:
    if not INPUT_CSV.exists():
        raise RuntimeError(f"Input CSV bulunamadi: {INPUT_CSV}")
    frame = pd.read_csv(INPUT_CSV, dtype=str, encoding="utf-8-sig").fillna("")
    frame.columns = [str(column).strip() for column in frame.columns]
    if SKU_COLUMN not in frame.columns:
        raise RuntimeError(f"SKU kolonu bulunamadi: {SKU_COLUMN}. Kolonlar: {list(frame.columns)}")

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        sku = clean_identifier(row.get(SKU_COLUMN, ""))
        if not sku or sku in seen:
            continue
        seen.add(sku)
        rows.append({
            "input_sku": sku,
            "input_ean": clean_identifier(row.get(EAN_COLUMN, "")),
            "input_name": str(row.get(NAME_COLUMN, "")).strip(),
        })
    if not rows:
        raise RuntimeError("CSV icinde gecerli SKU bulunamadi.")
    log(f"Input CSV: {len(rows)} benzersiz SKU")
    return rows


def input_hash(rows: list[dict[str, str]]) -> str:
    text = "\n".join(f"{r['input_sku']}|{r['input_ean']}" for r in rows)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_state(source_hash: str) -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "input_hash": source_hash,
        "next_index": 0,
        "next_sku": "",
        "cycle": 1,
        "completed_cycles": 0,
        "last_completed_cycle_at": "",
        "last_run_started_at": "",
        "last_run_finished_at": "",
        "last_stop_reason": "new",
        "last_processed_count": 0,
    }


def load_state(rows: list[dict[str, str]], source_hash: str) -> dict[str, Any]:
    state = default_state(source_hash)
    if STATE_JSON.exists():
        try:
            loaded = json.loads(STATE_JSON.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update(loaded)
        except (OSError, ValueError) as error:
            log(f"State okunamadi; sifirdan baslanacak: {error}")

    if state.get("input_hash") != source_hash:
        old_next_sku = clean_identifier(state.get("next_sku", ""))
        state = default_state(source_hash)
        if old_next_sku:
            lookup = {row["input_sku"]: index for index, row in enumerate(rows)}
            if old_next_sku in lookup:
                state["next_index"] = lookup[old_next_sku]
                state["next_sku"] = old_next_sku
                log(f"CSV degisti; kaldigi SKU bulundu: {old_next_sku}")
    state["input_hash"] = source_hash
    state["version"] = APP_VERSION
    state["next_index"] = max(0, min(int(state.get("next_index", 0) or 0), len(rows)))
    return state


def empty_output_row(source: dict[str, str]) -> dict[str, Any]:
    row = {column: "" for column in OUTPUT_COLUMNS}
    row.update(source)
    row["cycle"] = 0
    row["attempts_total"] = 0
    return row


def load_output_map(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    result = {row["input_sku"]: empty_output_row(row) for row in rows}
    if not OUTPUT_CSV.exists():
        return result
    try:
        frame = pd.read_csv(OUTPUT_CSV, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception as error:
        log(f"Onceki output okunamadi: {error}")
        return result
    for _, row in frame.iterrows():
        sku = clean_identifier(row.get("input_sku", ""))
        if sku in result:
            merged = result[sku]
            merged.update({str(k): v for k, v in row.to_dict().items()})
            result[sku] = merged
    return result


def save_all(output_map: dict[str, dict[str, Any]], input_rows: list[dict[str, str]], state: dict[str, Any], summary: dict[str, Any]) -> None:
    ordered = [output_map[row["input_sku"]] for row in input_rows]
    atomic_write_csv(OUTPUT_CSV, ordered, OUTPUT_COLUMNS)
    failed = [
        row for row in ordered
        if row.get("fetched_at_utc")
        and row.get("overall_status") not in {"SYNCED", "NO_CHANGE", "DRY_RUN_OK"}
    ]
    atomic_write_csv(FAILED_CSV, failed, OUTPUT_COLUMNS)
    atomic_write_json(STATE_JSON, state)
    atomic_write_json(SUMMARY_JSON, summary)


# ==========================================================
# ACTIVE SHOP CLIENT
# ==========================================================


class ActiveShopClient:
    def __init__(self) -> None:
        self.session = make_session()
        if ACTIVESHOP_PROXY_URL:
            self.session.proxies.update({"http": ACTIVESHOP_PROXY_URL, "https": ACTIVESHOP_PROXY_URL})
        self.token = ""

    def login(self) -> None:
        if not ACTIVESHOP_USERNAME or not ACTIVESHOP_PASSWORD:
            raise RuntimeError("ACTIVESHOP_USERNAME ve ACTIVESHOP_PASSWORD GitHub Secrets olarak gerekli.")
        url = f"{ACTIVESHOP_HOST}/rest/{ACTIVESHOP_STORE_CODE}/V1/integration/customer/token"
        response = self.session.post(
            url,
            json={"username": ACTIVESHOP_USERNAME, "password": ACTIVESHOP_PASSWORD},
            headers={"Content-Type": "application/json"},
            timeout=ACTIVESHOP_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ActiveShop token alinamadi: HTTP {response.status_code} - {response_error_text(response)}")
        token = response.json()
        if not isinstance(token, str) or not token:
            raise RuntimeError("ActiveShop token cevabi beklenmeyen formatta.")
        self.token = token
        log("ActiveShop token alindi.")

    def _get_json(
        self,
        path: str,
        *,
        sku: str,
        params: dict[str, Any] | None = None,
        not_found_status: str = "NOT_FOUND",
    ) -> dict[str, Any]:
        if not self.token:
            self.login()
        url = f"{ACTIVESHOP_HOST}/rest/{ACTIVESHOP_STORE_CODE}/{path.lstrip('/')}"
        rate_attempt = 0
        transient_attempt = 0
        total_attempts = 0
        refreshed = False

        while True:
            total_attempts += 1
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                    timeout=ACTIVESHOP_TIMEOUT,
                )
            except requests.RequestException as error:
                if transient_attempt >= 3:
                    return {"status": "NETWORK_ERROR", "error": str(error), "http_status": "", "attempts": total_attempts}
                wait = min(10 * (2 ** transient_attempt), 90)
                transient_attempt += 1
                log(f"ActiveShop ag hatasi | SKU {sku} | {wait}s bekleniyor")
                time.sleep(wait)
                continue

            if response.status_code == 401 and not refreshed:
                refreshed = True
                self.login()
                continue

            if is_activeshop_limit(response):
                if rate_attempt >= ACTIVESHOP_RATE_RETRIES:
                    return {
                        "status": "REQUEST_LIMIT_REACHED",
                        "error": response_error_text(response),
                        "http_status": response.status_code,
                        "attempts": total_attempts,
                    }
                wait = min(ACTIVESHOP_RATE_BASE_WAIT * (2 ** rate_attempt), ACTIVESHOP_RATE_MAX_WAIT)
                wait += random.uniform(0, 5)
                rate_attempt += 1
                log(f"ActiveShop hiz limiti | SKU {sku} | {wait:.1f}s sonra ayni SKU tekrar")
                time.sleep(wait)
                continue

            if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                if transient_attempt >= 3:
                    return {
                        "status": f"HTTP_{response.status_code}",
                        "error": response_error_text(response),
                        "http_status": response.status_code,
                        "attempts": total_attempts,
                    }
                wait = min(10 * (2 ** transient_attempt), 90)
                transient_attempt += 1
                time.sleep(wait)
                continue

            if response.status_code == 404:
                return {
                    "status": not_found_status,
                    "error": response_error_text(response),
                    "http_status": 404,
                    "attempts": total_attempts,
                }
            if response.status_code != 200:
                return {"status": f"HTTP_{response.status_code}", "error": response_error_text(response), "http_status": response.status_code, "attempts": total_attempts}

            try:
                payload = response.json()
            except ValueError:
                return {"status": "JSON_ERROR", "error": response.text[:1000], "http_status": 200, "attempts": total_attempts}
            if ACTIVESHOP_REQUEST_SLEEP:
                time.sleep(ACTIVESHOP_REQUEST_SLEEP + random.uniform(0, ACTIVESHOP_REQUEST_JITTER))
            return {"status": "OK", "data": payload, "error": "", "http_status": 200, "attempts": total_attempts}

    def get_product(self, sku: str) -> dict[str, Any]:
        result = self._get_json(
            f"V1/catalogProducts/{quote(sku, safe='')}",
            sku=sku,
            not_found_status="SKU_NOT_FOUND",
        )
        if result.get("status") == "OK" and not isinstance(result.get("data"), dict):
            return {
                "status": "UNEXPECTED_RESPONSE",
                "error": str(result.get("data"))[:1000],
                "http_status": 200,
                "attempts": result.get("attempts", 1),
            }
        return result

    def get_stock_fallback(self, sku: str) -> StockFetchResult:
        if not ACTIVESHOP_STOCK_FALLBACK:
            return StockFetchResult(None, "", "FALLBACK_DISABLED")

        attempts = 0
        errors: list[str] = []
        encoded_sku = quote(sku, safe="")

        for endpoint in ACTIVESHOP_STOCK_ENDPOINT_ORDER:
            if endpoint == "salable":
                if not ACTIVESHOP_STOCK_ID.isdigit():
                    errors.append("salable: ACTIVESHOP_STOCK_ID sayisal degil")
                    continue
                result = self._get_json(
                    f"V1/inventory/get-product-salable-quantity/{encoded_sku}/{ACTIVESHOP_STOCK_ID}",
                    sku=sku,
                    not_found_status="STOCK_NOT_FOUND",
                )
                attempts += int(result.get("attempts", 0) or 0)
                if result.get("status") == "REQUEST_LIMIT_REACHED":
                    return StockFetchResult(None, "", "REQUEST_LIMIT_REACHED", attempts, result.get("error", ""))
                if result.get("status") == "OK":
                    quantity, path = extract_stock_from_payload(result.get("data"), "inventory.salable.")
                    if quantity is not None:
                        return StockFetchResult(quantity, path, "FALLBACK_SALABLE", attempts)
                    errors.append("salable: cevapta miktar bulunamadi")
                else:
                    errors.append(f"salable: {result.get('status')} {result.get('error', '')}".strip())

            elif endpoint == "legacy":
                result = self._get_json(
                    f"V1/stockItems/{encoded_sku}",
                    sku=sku,
                    not_found_status="STOCK_NOT_FOUND",
                )
                attempts += int(result.get("attempts", 0) or 0)
                if result.get("status") == "REQUEST_LIMIT_REACHED":
                    return StockFetchResult(None, "", "REQUEST_LIMIT_REACHED", attempts, result.get("error", ""))
                if result.get("status") == "OK":
                    quantity, path = extract_stock_from_payload(result.get("data"), "stockItems.")
                    if quantity is not None:
                        return StockFetchResult(quantity, path, "FALLBACK_LEGACY", attempts)
                    errors.append("legacy: cevapta miktar bulunamadi")
                else:
                    errors.append(f"legacy: {result.get('status')} {result.get('error', '')}".strip())

            elif endpoint == "source_items":
                params = {
                    "searchCriteria[filter_groups][0][filters][0][field]": "sku",
                    "searchCriteria[filter_groups][0][filters][0][value]": sku,
                    "searchCriteria[filter_groups][0][filters][0][condition_type]": "eq",
                    "searchCriteria[pageSize]": 100,
                }
                result = self._get_json(
                    "V1/inventory/source-items",
                    sku=sku,
                    params=params,
                    not_found_status="STOCK_NOT_FOUND",
                )
                attempts += int(result.get("attempts", 0) or 0)
                if result.get("status") == "REQUEST_LIMIT_REACHED":
                    return StockFetchResult(None, "", "REQUEST_LIMIT_REACHED", attempts, result.get("error", ""))
                if result.get("status") == "OK":
                    payload = result.get("data")
                    items = payload.get("items", []) if isinstance(payload, dict) else []
                    total = 0.0
                    matched = 0
                    if isinstance(items, list):
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            source_code = str(item.get("source_code", "")).strip()
                            if ACTIVESHOP_SOURCE_CODES and source_code not in ACTIVESHOP_SOURCE_CODES:
                                continue
                            item_sku = clean_identifier(item.get("sku"))
                            if item_sku and item_sku != sku:
                                continue
                            quantity = to_float(item.get("quantity"))
                            if quantity is None:
                                continue
                            status = item.get("status", 1)
                            if status is False or status == 0 or str(status).strip().lower() in {"false", "0"}:
                                quantity = 0.0
                            total += max(0.0, quantity)
                            matched += 1
                    if matched:
                        suffix = ",".join(sorted(ACTIVESHOP_SOURCE_CODES)) if ACTIVESHOP_SOURCE_CODES else "all"
                        return StockFetchResult(total, f"inventory.source_items[{suffix}]", "FALLBACK_SOURCE_ITEMS", attempts)
                    errors.append("source_items: eslesen kaynak stogu bulunamadi")
                else:
                    errors.append(f"source_items: {result.get('status')} {result.get('error', '')}".strip())
            else:
                errors.append(f"bilinmeyen stok endpoint tipi: {endpoint}")

        return StockFetchResult(
            None,
            "",
            "SOURCE_STOCK_MISSING",
            attempts,
            " | ".join(errors)[:2500],
        )


# ==========================================================
# PLENTY CLIENT
# ==========================================================


class PlentyClient:
    def __init__(self) -> None:
        self.session = make_session()
        self.token = ""
        self.last_write_at = 0.0

    def login(self) -> None:
        if not PLENTY_BASE_URL or not PLENTY_USERNAME or not PLENTY_PASSWORD:
            raise RuntimeError("PLENTY_BASE_URL, PLENTY_USERNAME ve PLENTY_PASSWORD GitHub Secrets olarak gerekli.")
        response = self.session.post(
            f"{PLENTY_BASE_URL}/rest/login",
            json={"username": PLENTY_USERNAME, "password": PLENTY_PASSWORD},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=PLENTY_TIMEOUT,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"Plenty login basarisiz: HTTP {response.status_code} - {response_error_text(response)}")
        payload = response.json()
        token = payload.get("access_token") if isinstance(payload, dict) else ""
        if not token:
            raise RuntimeError("Plenty login cevabinda access_token bulunamadi.")
        self.token = str(token)
        log("Plenty access token alindi.")

    def _throttle_write(self) -> None:
        if PLENTY_WRITE_INTERVAL <= 0:
            return
        elapsed = time.monotonic() - self.last_write_at
        wait = PLENTY_WRITE_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        is_write: bool = False,
    ) -> requests.Response:
        if not self.token:
            self.login()

        refreshed = False
        attempt = 0
        while True:
            if is_write:
                self._throttle_write()
            response = self.session.request(
                method,
                f"{PLENTY_BASE_URL}{path}",
                params=params,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=PLENTY_TIMEOUT,
            )
            if is_write:
                self.last_write_at = time.monotonic()
            elif PLENTY_READ_SLEEP:
                time.sleep(PLENTY_READ_SLEEP)

            if response.status_code == 401 and not refreshed:
                refreshed = True
                self.login()
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < PLENTY_MAX_RETRIES:
                fallback = min(5 * (2 ** attempt), 120)
                wait = retry_after_seconds(response, fallback)
                attempt += 1
                log(f"Plenty gecici hata HTTP {response.status_code} | {wait:.1f}s bekleniyor | deneme {attempt}/{PLENTY_MAX_RETRIES}")
                time.sleep(wait)
                continue
            return response

    def search_variation(self, sku: str, ean: str) -> dict[str, Any]:
        methods: list[tuple[str, dict[str, Any]]] = [
            ("numberExact", {"numberExact": sku}),
            ("sku", {"sku": sku}),
        ]
        if ean:
            methods.append(("barcode", {"barcode": ean}))

        seen: dict[str, dict[str, Any]] = {}
        matches_by_method: dict[str, list[dict[str, Any]]] = {}
        for method_name, filter_params in methods:
            params = {
                **filter_params,
                "itemsPerPage": 20,
                "with": "variationSalesPrices,stock,variationBarcodes",
            }
            response = self.request("GET", "/rest/items/variations", params=params)
            if response.status_code != 200:
                return {
                    "status": f"PLENTY_SEARCH_HTTP_{response.status_code}",
                    "error": response_error_text(response),
                    "variation": None,
                    "match_method": method_name,
                }
            payload = response.json()
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            if not isinstance(entries, list):
                entries = []
            matches_by_method[method_name] = [item for item in entries if isinstance(item, dict)]
            for item in matches_by_method[method_name]:
                key = clean_identifier(item.get("id"))
                if key:
                    seen[key] = item

            exact = [item for item in matches_by_method[method_name] if clean_identifier(item.get("number")) == sku]
            if method_name == "numberExact" and len(exact) == 1:
                return {"status": "OK", "error": "", "variation": exact[0], "match_method": method_name}
            if len(matches_by_method[method_name]) == 1:
                return {"status": "OK", "error": "", "variation": matches_by_method[method_name][0], "match_method": method_name}

        if len(seen) == 1:
            return {"status": "OK", "error": "", "variation": next(iter(seen.values())), "match_method": "combined"}
        if not seen:
            return {"status": "PLENTY_VARIATION_NOT_FOUND", "error": "SKU/EAN ile Plenty varyasyonu bulunamadi.", "variation": None, "match_method": ""}
        return {
            "status": "PLENTY_VARIATION_AMBIGUOUS",
            "error": f"SKU/EAN birden fazla Plenty varyasyonuna eslesti: {list(seen.keys())}",
            "variation": None,
            "match_method": "",
        }

    def list_variation_stock(self, item_id: int, variation_id: int) -> list[dict[str, Any]]:
        response = self.request("GET", f"/rest/items/{item_id}/variations/{variation_id}/stock")
        if response.status_code != 200:
            raise RuntimeError(f"Plenty stok okunamadi: HTTP {response.status_code} - {response_error_text(response)}")
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            entries = payload.get("entries") or payload.get("data") or []
            if isinstance(entries, list):
                return [item for item in entries if isinstance(item, dict)]
        return []

    def update_purchase_price(self, item_id: int, variation_id: int, price: float) -> None:
        response = self.request(
            "PUT",
            f"/rest/items/{item_id}/variations/{variation_id}",
            json_body={"purchasePrice": price},
            is_write=True,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"Plenty purchasePrice guncellenemedi: HTTP {response.status_code} - {response_error_text(response)}")

    def correct_stock(self, item_id: int, variation_id: int, quantity_delta: float) -> None:
        response = self.request(
            "PUT",
            f"/rest/items/{item_id}/variations/{variation_id}/stock/correction",
            params={"itemId": item_id},
            json_body={
                "quantity": quantity_delta,
                "warehouseId": int(PLENTY_WAREHOUSE_ID),
                "storageLocationId": int(PLENTY_STORAGE_LOCATION_ID),
                "reasonId": STOCK_CORRECTION_REASON_ID,
            },
            is_write=True,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"Plenty stok guncellenemedi: HTTP {response.status_code} - {response_error_text(response)}")

    def upsert_sales_price(self, variation: dict[str, Any], variation_id: int, price: float) -> None:
        sales_price_id = int(PLENTY_SALES_PRICE_ID)
        relations = variation.get("variationSalesPrices") or variation.get("variation_sales_prices") or []
        exists = False
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                candidate = relation.get("salesPriceId") or relation.get("sales_price_id")
                if clean_identifier(candidate) == str(sales_price_id):
                    exists = True
                    break
        payload = [{"variationId": variation_id, "salesPriceId": sales_price_id, "price": price}]
        response = self.request(
            "PUT" if exists else "POST",
            "/rest/items/variations/variation_sales_prices",
            json_body=payload,
            is_write=True,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"Plenty satis fiyati guncellenemedi: HTTP {response.status_code} - {response_error_text(response)}")

    def discover(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, path in (
            ("warehouses", "/rest/stockmanagement/warehouses"),
            ("sales_prices", "/rest/items/sales_prices"),
        ):
            response = self.request("GET", path)
            result[name] = response.json() if response.status_code == 200 else {
                "http_status": response.status_code,
                "error": response_error_text(response),
            }
        return result


# ==========================================================
# PLENTY VALUE EXTRACTION
# ==========================================================


def get_plenty_purchase_price(variation: dict[str, Any]) -> float | None:
    return to_float(variation.get("purchasePrice") or variation.get("purchase_price"))


def get_plenty_stock_for_warehouse(stock_entries: list[dict[str, Any]], warehouse_id: int) -> float:
    candidates: list[float] = []
    for entry in stock_entries:
        wid = entry.get("warehouseId") or entry.get("warehouse_id") or entry.get("warehouse")
        if isinstance(wid, dict):
            wid = wid.get("id")
        if clean_identifier(wid) != str(warehouse_id):
            continue
        for key in ("stockPhysical", "stock_physical", "physicalStock", "physical_stock", "quantity", "stock"):
            value = to_float(entry.get(key))
            if value is not None:
                candidates.append(value)
                break
    return sum(candidates) if candidates else 0.0


def source_stock_to_target(value: float) -> float:
    target = max(0.0, value - STOCK_SAFETY_DEDUCTION)
    target = min(target, STOCK_MAXIMUM)
    return round(target, 4)


# ==========================================================
# VALIDATION
# ==========================================================


def validate_config(require_credentials: bool = True, require_target_ids: bool = True) -> None:
    errors: list[str] = []
    valid_stock_endpoints = {"salable", "legacy", "source_items"}
    if require_credentials:
        if not ACTIVESHOP_USERNAME or not ACTIVESHOP_PASSWORD:
            errors.append("ACTIVESHOP_USERNAME / ACTIVESHOP_PASSWORD eksik")
        if not PLENTY_BASE_URL or not PLENTY_USERNAME or not PLENTY_PASSWORD:
            errors.append("PLENTY_BASE_URL / PLENTY_USERNAME / PLENTY_PASSWORD eksik")
    if require_target_ids and UPDATE_STOCK:
        if not PLENTY_WAREHOUSE_ID.isdigit():
            errors.append("UPDATE_STOCK=true iken PLENTY_WAREHOUSE_ID sayisal olmali")
        if not PLENTY_STORAGE_LOCATION_ID.isdigit():
            errors.append("UPDATE_STOCK=true iken PLENTY_STORAGE_LOCATION_ID sayisal olmali")
    if require_target_ids and UPDATE_SALES_PRICE and not PLENTY_SALES_PRICE_ID.isdigit():
        errors.append("UPDATE_SALES_PRICE=true iken PLENTY_SALES_PRICE_ID sayisal olmali")
    if UPDATE_STOCK and ACTIVESHOP_STOCK_FALLBACK:
        unknown = [name for name in ACTIVESHOP_STOCK_ENDPOINT_ORDER if name not in valid_stock_endpoints]
        if unknown:
            errors.append(f"Bilinmeyen ACTIVESHOP_STOCK_ENDPOINT_ORDER degeri: {unknown}")
        if "salable" in ACTIVESHOP_STOCK_ENDPOINT_ORDER and not ACTIVESHOP_STOCK_ID.isdigit():
            errors.append("salable stok sorgusu icin ACTIVESHOP_STOCK_ID sayisal olmali")
    if STOCK_SAFETY_DEDUCTION < 0:
        errors.append("STOCK_SAFETY_DEDUCTION negatif olamaz")
    if STOCK_MAXIMUM < 0:
        errors.append("STOCK_MAXIMUM negatif olamaz")
    if errors:
        raise RuntimeError("Konfigurasyon hatasi: " + "; ".join(errors))


# ==========================================================
# MAIN SYNC
# ==========================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ActiveShop price/stock -> Plenty direct synchronization")
    parser.add_argument("--validate-only", action="store_true", help="CSV and environment validation only")
    parser.add_argument("--reset-progress", action="store_true", help="Reset progress to first SKU")
    parser.add_argument("--discover-plenty", action="store_true", help="List Plenty warehouses and sales prices")
    parser.add_argument("--test-sku", default="", help="Only process one SKU without changing progress")
    return parser.parse_args()


def build_base_row(source: dict[str, str], previous: dict[str, Any], cycle: int, attempts: int) -> dict[str, Any]:
    row = {column: previous.get(column, "") for column in OUTPUT_COLUMNS}
    row.update(source)
    row["cycle"] = cycle
    row["attempts_total"] = int(previous.get("attempts_total", 0) or 0) + attempts
    row["fetched_at_utc"] = utc_iso()
    row["synced_at_utc"] = ""
    row["error"] = ""
    return row


def process_one(
    source: dict[str, str],
    previous: dict[str, Any],
    cycle: int,
    active: ActiveShopClient,
    plenty: PlentyClient,
) -> tuple[dict[str, Any], bool]:
    sku = source["input_sku"]
    active_result = active.get_product(sku)
    attempts = int(active_result.get("attempts", 1))
    row = build_base_row(source, previous, cycle, attempts)

    if active_result["status"] == "REQUEST_LIMIT_REACHED":
        row["source_status"] = "REQUEST_LIMIT_REACHED"
        row["overall_status"] = "SOURCE_RATE_LIMIT"
        row["error"] = active_result.get("error", "")
        return row, True

    if active_result["status"] != "OK":
        row["source_status"] = active_result["status"]
        row["overall_status"] = "SOURCE_ERROR"
        row["error"] = active_result.get("error", "")
        return row, False

    values = extract_source_values(active_result["data"], sku, source["input_ean"])
    stock_fetch_status = "PRODUCT_PAYLOAD" if values.stock is not None else "NOT_REQUESTED"
    stock_fetch_error = ""
    if UPDATE_STOCK and values.stock is None:
        stock_result = active.get_stock_fallback(sku)
        row["attempts_total"] = int(row.get("attempts_total", 0) or 0) + stock_result.attempts
        stock_fetch_status = stock_result.status
        stock_fetch_error = stock_result.error
        if stock_result.status == "REQUEST_LIMIT_REACHED":
            row.update({
                "source_stock_status": stock_result.status,
                "source_status": "REQUEST_LIMIT_REACHED",
                "overall_status": "SOURCE_RATE_LIMIT",
                "error": stock_result.error,
            })
            return row, True
        if stock_result.quantity is not None:
            values.stock = stock_result.quantity
            values.stock_path = stock_result.path

    row.update({
        "source_api_sku": values.api_sku,
        "source_api_ean": values.api_ean,
        "source_api_name": values.api_name,
        "source_currency": values.currency,
        "source_price_diamond": "" if values.price is None else values.price,
        "source_price_path": values.price_path,
        "source_stock": "" if values.stock is None else values.stock,
        "source_stock_path": values.stock_path,
        "source_stock_status": stock_fetch_status,
        "source_status": "OK",
    })

    match = plenty.search_variation(sku, values.api_ean or source["input_ean"])
    if match["status"] != "OK":
        row["plenty_status"] = match["status"]
        row["overall_status"] = "PLENTY_MATCH_ERROR"
        row["error"] = match.get("error", "")
        return row, False

    variation = match["variation"]
    raw_item_id = variation.get("itemId") or variation.get("item_id")
    raw_variation_id = variation.get("id")
    if to_float(raw_item_id) is None or to_float(raw_variation_id) is None:
        row["plenty_status"] = "PLENTY_RESPONSE_MISSING_IDS"
        row["overall_status"] = "PLENTY_MATCH_ERROR"
        row["error"] = "Plenty varyasyon cevabinda itemId veya variation id bulunamadi."
        return row, False
    item_id = int(float(raw_item_id))
    variation_id = int(float(raw_variation_id))
    variation_number = clean_identifier(variation.get("number"))
    row.update({
        "plenty_item_id": item_id,
        "plenty_variation_id": variation_id,
        "plenty_variation_number": variation_number,
        "plenty_match_method": match.get("match_method", ""),
        "plenty_status": "FOUND",
    })

    errors: list[str] = []
    changed = False
    dry_planned = False

    # Purchase price
    old_purchase = get_plenty_purchase_price(variation)
    row["plenty_old_purchase_price"] = "" if old_purchase is None else old_purchase
    if UPDATE_PURCHASE_PRICE:
        if values.price is None:
            row["purchase_price_status"] = "SOURCE_PRICE_MISSING"
            errors.append("ActiveShop Price Diamond bulunamadi")
        else:
            target_purchase = round_price(values.price * PURCHASE_PRICE_MULTIPLIER + PURCHASE_PRICE_ADD)
            row["plenty_target_purchase_price"] = target_purchase
            if old_purchase is not None and abs(old_purchase - target_purchase) < 0.0001:
                row["purchase_price_status"] = "NO_CHANGE"
            elif not PLENTY_ENABLE_WRITE:
                row["purchase_price_status"] = "DRY_RUN_UPDATE"
                dry_planned = True
            else:
                try:
                    plenty.update_purchase_price(item_id, variation_id, target_purchase)
                    row["purchase_price_status"] = "UPDATED"
                    changed = True
                except Exception as error:
                    row["purchase_price_status"] = "ERROR"
                    errors.append(str(error))
    else:
        row["purchase_price_status"] = "DISABLED"

    # Stock
    if UPDATE_STOCK:
        if values.stock is None:
            row["stock_status"] = "SOURCE_STOCK_MISSING"
            message = "ActiveShop stok bilgisi bulunamadi"
            if stock_fetch_error:
                message += f" ({stock_fetch_error})"
            errors.append(message)
        else:
            try:
                stock_entries = plenty.list_variation_stock(item_id, variation_id)
                old_stock = get_plenty_stock_for_warehouse(stock_entries, int(PLENTY_WAREHOUSE_ID))
                target_stock = source_stock_to_target(values.stock)
                delta = round(target_stock - old_stock, 4)
                row["plenty_old_stock"] = old_stock
                row["plenty_target_stock"] = target_stock
                row["stock_delta"] = delta
                if abs(delta) < 0.0001:
                    row["stock_status"] = "NO_CHANGE"
                elif not PLENTY_ENABLE_WRITE:
                    row["stock_status"] = "DRY_RUN_CORRECTION"
                    dry_planned = True
                else:
                    plenty.correct_stock(item_id, variation_id, delta)
                    row["stock_status"] = "UPDATED"
                    changed = True
            except Exception as error:
                row["stock_status"] = "ERROR"
                errors.append(str(error))
    else:
        row["stock_status"] = "DISABLED"

    # Optional sales price (off by default)
    if UPDATE_SALES_PRICE:
        row["plenty_sales_price_id"] = PLENTY_SALES_PRICE_ID
        if values.price is None:
            row["sales_price_status"] = "SOURCE_PRICE_MISSING"
        else:
            target_sales = round_price(values.price * SALES_PRICE_MULTIPLIER + SALES_PRICE_ADD)
            row["plenty_target_sales_price"] = target_sales
            if not PLENTY_ENABLE_WRITE:
                row["sales_price_status"] = "DRY_RUN_UPSERT"
                dry_planned = True
            else:
                try:
                    plenty.upsert_sales_price(variation, variation_id, target_sales)
                    row["sales_price_status"] = "UPDATED"
                    changed = True
                except Exception as error:
                    row["sales_price_status"] = "ERROR"
                    errors.append(str(error))
    else:
        row["sales_price_status"] = "DISABLED"

    row["synced_at_utc"] = utc_iso()
    if errors:
        row["overall_status"] = "PARTIAL_ERROR"
        row["error"] = " | ".join(errors)[:4000]
    elif not PLENTY_ENABLE_WRITE and dry_planned:
        row["overall_status"] = "DRY_RUN_OK"
    elif changed:
        row["overall_status"] = "SYNCED"
    else:
        row["overall_status"] = "NO_CHANGE"
    return row, False


def main() -> int:
    args = parse_args()
    started = utc_now()
    log("====================================================")
    log(f"ActiveShop -> Plenty Direct Sync V{APP_VERSION} basladi")
    log(f"Yazma modu: {'ACIK' if PLENTY_ENABLE_WRITE else 'KAPALI (DRY RUN)'}")
    log(f"Purchase price: {UPDATE_PURCHASE_PRICE} | Stock: {UPDATE_STOCK} | Sales price: {UPDATE_SALES_PRICE}")
    log("====================================================")

    rows = read_input_rows()
    if args.discover_plenty:
        # Discovery only needs Plenty credentials; warehouse/location IDs are what we are trying to find.
        if not PLENTY_BASE_URL or not PLENTY_USERNAME or not PLENTY_PASSWORD:
            raise RuntimeError("PLENTY_BASE_URL, PLENTY_USERNAME ve PLENTY_PASSWORD gerekli.")
    else:
        validate_config(require_credentials=True, require_target_ids=True)
    source_hash = input_hash(rows)
    state = load_state(rows, source_hash)
    output_map = load_output_map(rows)

    if args.reset_progress:
        state = default_state(source_hash)
        state["next_sku"] = rows[0]["input_sku"]
        log("Progress ilk SKU'ya sifirlandi.")

    if args.validate_only:
        log(f"Validation OK | SKU={len(rows)} | next_index={state['next_index']} | cycle={state['cycle']}")
        return 0

    plenty = PlentyClient()
    plenty.login()

    if args.discover_plenty:
        discovered = plenty.discover()
        print(json.dumps(discovered, ensure_ascii=False, indent=2))
        return 0

    active = ActiveShopClient()
    active.login()

    if args.test_sku:
        test_sku = clean_identifier(args.test_sku)
        source = next((row for row in rows if row["input_sku"] == test_sku), None)
        if not source:
            raise RuntimeError(f"Test SKU CSV'de bulunamadi: {test_sku}")
        result, _ = process_one(source, output_map[test_sku], int(state.get("cycle", 1)), active, plenty)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("overall_status") in {"SYNCED", "NO_CHANGE", "DRY_RUN_OK"} else 1

    state["last_run_started_at"] = started.isoformat()
    state["last_stop_reason"] = "running"
    state["last_processed_count"] = 0

    total = len(rows)
    current_index = int(state.get("next_index", 0) or 0)
    persisted_start_index = current_index
    cycle = int(state.get("cycle", 1) or 1)
    processed = 0
    success_count = 0
    error_count = 0
    dry_run_count = 0
    stop_reason = "batch_completed"

    while processed < MAX_PRODUCTS_PER_RUN:
        elapsed_minutes = (utc_now() - started).total_seconds() / 60
        if elapsed_minutes >= MAX_RUN_MINUTES:
            stop_reason = "max_run_time"
            log(f"Maksimum calisma suresi doldu: {MAX_RUN_MINUTES} dakika")
            break

        if current_index >= total:
            current_index = 0
            state["completed_cycles"] = int(state.get("completed_cycles", 0) or 0) + 1
            state["last_completed_cycle_at"] = utc_iso()
            cycle += 1
            state["cycle"] = cycle
            stop_reason = "cycle_completed"
            log("Tum SKU'lar tamamlandi. Sonraki otomatik calisma yeni turu baslatacak.")
            break

        source = rows[current_index]
        sku = source["input_sku"]
        log(f"Tur {cycle} | {current_index + 1}/{total} | Bu calisma {processed + 1}/{MAX_PRODUCTS_PER_RUN} | SKU {sku}")

        row, stop_for_source_limit = process_one(source, output_map[sku], cycle, active, plenty)
        output_map[sku] = row

        if stop_for_source_limit:
            stop_reason = "activeshop_rate_limit"
            log(f"ActiveShop limiti devam ediyor. SKU atlanmadi: {sku}")
            break

        status = row.get("overall_status", "")
        if status in {"SYNCED", "NO_CHANGE"}:
            success_count += 1
            log(f"OK | {sku} | {status} | purchase={row.get('purchase_price_status')} | stock={row.get('stock_status')}")
        elif status == "DRY_RUN_OK":
            dry_run_count += 1
            log(f"DRY RUN | {sku} | purchase={row.get('purchase_price_status')} | stock={row.get('stock_status')}")
        else:
            error_count += 1
            log(f"HATA | {sku} | {status} | {row.get('error', '')[:500]}")

        current_index += 1
        processed += 1
        if PLENTY_ENABLE_WRITE:
            state["next_index"] = current_index
            state["next_sku"] = rows[current_index]["input_sku"] if current_index < total else ""
            state["cycle"] = cycle
        else:
            # Dry run must not consume progress. When write mode is enabled, synchronization starts at the same SKU.
            state["next_index"] = persisted_start_index
            state["next_sku"] = rows[persisted_start_index]["input_sku"] if persisted_start_index < total else ""
        state["last_processed_count"] = processed

        if processed % CHECKPOINT_EVERY == 0:
            interim_summary = {
                "version": APP_VERSION,
                "run_started_at": started.isoformat(),
                "checkpoint_at": utc_iso(),
                "write_enabled": PLENTY_ENABLE_WRITE,
                "processed": processed,
                "success": success_count,
                "dry_run": dry_run_count,
                "errors": error_count,
                "next_index": current_index,
                "cycle": cycle,
                "stop_reason": "running",
            }
            save_all(output_map, rows, state, interim_summary)
            log(f"Checkpoint kaydedildi: {processed} urun")

    finished = utc_now()
    if PLENTY_ENABLE_WRITE:
        state["next_index"] = current_index
        state["next_sku"] = rows[current_index]["input_sku"] if current_index < total else ""
        state["cycle"] = cycle
    else:
        current_index = persisted_start_index
        state["next_index"] = persisted_start_index
        state["next_sku"] = rows[persisted_start_index]["input_sku"] if persisted_start_index < total else ""
        stop_reason = f"dry_run_{stop_reason}"
    state["last_run_finished_at"] = finished.isoformat()
    state["last_stop_reason"] = stop_reason
    state["last_processed_count"] = processed

    summary = {
        "version": APP_VERSION,
        "run_started_at": started.isoformat(),
        "run_finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "write_enabled": PLENTY_ENABLE_WRITE,
        "update_purchase_price": UPDATE_PURCHASE_PRICE,
        "update_stock": UPDATE_STOCK,
        "update_sales_price": UPDATE_SALES_PRICE,
        "processed": processed,
        "success": success_count,
        "dry_run": dry_run_count,
        "errors": error_count,
        "next_index": current_index,
        "next_sku": state["next_sku"],
        "cycle": cycle,
        "stop_reason": stop_reason,
        "output_csv": str(OUTPUT_CSV),
    }
    save_all(output_map, rows, state, summary)

    append_step_summary([
        f"## ActiveShop -> Plenty Sync V{APP_VERSION}",
        f"- Yazma modu: **{'ACIK' if PLENTY_ENABLE_WRITE else 'DRY RUN'}**",
        f"- Islenen: **{processed}**",
        f"- Basarili / degisiklik yok: **{success_count}**",
        f"- Dry run: **{dry_run_count}**",
        f"- Hata: **{error_count}**",
        f"- Siradaki index: **{current_index}**",
        f"- Durma sebebi: **{stop_reason}**",
    ])

    log("====================================================")
    log(f"ISLEM BITTI | processed={processed} | success={success_count} | dry_run={dry_run_count} | errors={error_count}")
    log(f"Siradaki index={current_index} | stop_reason={stop_reason}")
    log("====================================================")
    # Partial item errors are kept in CSV and should not block state commits.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
