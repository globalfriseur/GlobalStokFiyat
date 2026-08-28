from __future__ import annotations

import atexit
import os
import re
import time
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import run_stock_sync as stock


# German B2B frontend lives below /de. Stock is shown to authenticated B2B
# customers as `Auf Lager: N Stck.`. The REST customer token is separate from
# the storefront session, therefore the stock runner establishes a real browser
# session when plain requests cannot see the stock block.
stock.ACTIVESHOP_FRONTEND_BASE_URL = os.getenv(
    "ACTIVESHOP_FRONTEND_BASE_URL",
    "https://b2b.activeshop.com.pl/de",
).strip().rstrip("/")

PUBLIC_FRONTEND_BASE_URL = os.getenv(
    "ACTIVESHOP_PUBLIC_FRONTEND_BASE_URL",
    "https://activeshop.com.pl/de",
).strip().rstrip("/")

BROWSER_NAV_TIMEOUT_MS = max(
    10000,
    int(float(os.getenv("ACTIVESHOP_BROWSER_NAV_TIMEOUT", "45")) * 1000),
)
BROWSER_STOCK_WAIT_SECONDS = max(
    1.0,
    float(os.getenv("ACTIVESHOP_BROWSER_STOCK_WAIT_SECONDS", "8")),
)

_original_frontend_url_candidates = stock._frontend_url_candidates


def frontend_url_candidates(data: dict[str, Any]) -> list[str]:
    """Prefer the authenticated German B2B detail URL, then public fallback."""
    original = list(_original_frontend_url_candidates(data))
    custom = stock.sync.custom_attributes_to_dict(data)
    ext = data.get("extension_attributes") or {}
    if not isinstance(ext, dict):
        ext = {}

    generated: list[str] = []
    raw_keys = (
        data.get("url_key"),
        ext.get("url_key"),
        custom.get("urlkey"),
    )
    for raw in raw_keys:
        if raw in (None, "", [], {}):
            continue
        slug = str(raw).strip().strip("/")
        if not slug:
            continue
        if not slug.lower().endswith(".html"):
            slug += ".html"
        for base in (stock.ACTIVESHOP_FRONTEND_BASE_URL, PUBLIC_FRONTEND_BASE_URL):
            url = urljoin(base + "/", slug)
            if url not in generated:
                generated.append(url)

    # Put the known-good /de B2B route first. Keep any API-provided absolute URL
    # afterwards only as a fallback.
    result: list[str] = []
    for url in generated + original:
        if url and url not in result:
            result.append(url)
    return result


stock._frontend_url_candidates = frontend_url_candidates


def _b2b_candidates(data: dict[str, Any]) -> list[str]:
    urls = frontend_url_candidates(data)
    preferred = [
        url for url in urls
        if url.startswith(stock.ACTIVESHOP_FRONTEND_BASE_URL + "/")
    ]
    return preferred or urls


def _playwright_proxy() -> dict[str, str] | None:
    raw = str(getattr(stock.sync, "ACTIVESHOP_PROXY_URL", "") or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return {"server": raw}
    host = parsed.hostname
    if parsed.port:
        host += f":{parsed.port}"
    proxy: dict[str, str] = {"server": f"{parsed.scheme}://{host}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def _browser_close(self) -> None:
    for name in ("_b2b_browser", "_b2b_playwright"):
        resource = getattr(self, name, None)
        if resource is None:
            continue
        try:
            if name == "_b2b_browser":
                resource.close()
            else:
                resource.stop()
        except Exception:
            pass
        setattr(self, name, None)


def _ensure_browser(self):
    page = getattr(self, "_b2b_page", None)
    if page is not None:
        return page

    from playwright.sync_api import sync_playwright

    self._b2b_playwright = sync_playwright().start()
    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    }
    proxy = _playwright_proxy()
    if proxy:
        launch_kwargs["proxy"] = proxy

    self._b2b_browser = self._b2b_playwright.chromium.launch(**launch_kwargs)
    self._b2b_context = self._b2b_browser.new_context(
        locale="de-DE",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
    )
    self._b2b_page = self._b2b_context.new_page()
    return self._b2b_page


def _copy_browser_cookies_to_requests(self) -> int:
    context = getattr(self, "_b2b_context", None)
    if context is None:
        return 0
    copied = 0
    for cookie in context.cookies():
        name = str(cookie.get("name") or "")
        if not name:
            continue
        kwargs: dict[str, Any] = {}
        domain = str(cookie.get("domain") or "").strip()
        path = str(cookie.get("path") or "/")
        if domain:
            kwargs["domain"] = domain
        if path:
            kwargs["path"] = path
        try:
            self.session.cookies.set(name, str(cookie.get("value") or ""), **kwargs)
            copied += 1
        except Exception:
            # Host-only cookie fallback.
            try:
                self.session.cookies.set(name, str(cookie.get("value") or ""))
                copied += 1
            except Exception:
                pass
    return copied


def _choose_b2b_login_form(page):
    forms = page.locator("form")
    fallback = None
    for index in range(forms.count()):
        form = forms.nth(index)
        if form.locator("input[type='password']").count() == 0:
            continue
        if fallback is None:
            fallback = form
        action = str(form.get_attribute("action") or "").lower()
        try:
            text = str(form.inner_text(timeout=2000) or "").lower()
        except Exception:
            text = ""
        if "customerb2b" in action or "b2b" in text or "geschäftskonto" in text:
            return form
    return fallback


def b2b_frontend_login(self) -> bool:
    """Log into the B2B storefront with a real Chromium session and reuse cookies."""
    if getattr(self, "_frontend_login_attempted", False):
        return bool(getattr(self, "_frontend_logged_in", False))
    self._frontend_login_attempted = True

    login_url = f"{stock.ACTIVESHOP_FRONTEND_BASE_URL}/customerb2b/account/login/"
    try:
        page = _ensure_browser(self)
        response = page.goto(
            login_url,
            wait_until="domcontentloaded",
            timeout=BROWSER_NAV_TIMEOUT_MS,
        )
        status_code = int(response.status) if response is not None else 0
        form = _choose_b2b_login_form(page)
        if form is None:
            raise RuntimeError(
                f"B2B login formu bulunamadi: HTTP {status_code}, url={page.url}"
            )

        email = form.locator(
            "input[name='login[username]'], input[type='email'], "
            "input[name*='email' i], input[name*='username' i]"
        ).first
        password = form.locator(
            "input[name='login[password]'], input[type='password']"
        ).first
        if email.count() == 0 or password.count() == 0:
            raise RuntimeError("B2B login email/password alanlari bulunamadi")

        email.fill(stock.sync.ACTIVESHOP_USERNAME)
        password.fill(stock.sync.ACTIVESHOP_PASSWORD)

        submit = form.locator("button[type='submit'], input[type='submit']").first
        if submit.count() == 0:
            password.press("Enter")
        else:
            submit.click()

        try:
            page.wait_for_load_state("domcontentloaded", timeout=BROWSER_NAV_TIMEOUT_MS)
        except Exception:
            pass
        page.wait_for_timeout(1200)

        final_url = str(page.url or "")
        lower_url = final_url.lower()
        still_on_login = (
            "customerb2b/account/login" in lower_url
            or "customer/account/login" in lower_url
        )
        copied = _copy_browser_cookies_to_requests(self)
        self._frontend_logged_in = not still_on_login and copied > 0
        self.frontend_stock_meta["_login"] = {
            "status": "BROWSER_LOGIN_OK" if self._frontend_logged_in else "BROWSER_LOGIN_FAILED",
            "url": final_url,
            "cookies": copied,
            "error": "" if self._frontend_logged_in else "Login sayfasindan cikilamadi",
        }
        stock.sync.log(
            "B2B LOGIN | "
            f"durum={'OK' if self._frontend_logged_in else 'FAILED'} | "
            f"final_url={final_url} | cookies={copied}"
        )
        return self._frontend_logged_in
    except Exception as error:
        self._frontend_logged_in = False
        self.frontend_stock_meta["_login"] = {
            "status": "BROWSER_LOGIN_ERROR",
            "url": login_url,
            "cookies": 0,
            "error": str(error),
        }
        stock.sync.log(f"B2B LOGIN HATA | {type(error).__name__}: {error}")
        return False


_original_client_init = stock.StockOnlyActiveShopClient.__init__


def b2b_client_init(self) -> None:
    _original_client_init(self)
    self._b2b_playwright = None
    self._b2b_browser = None
    self._b2b_context = None
    self._b2b_page = None
    atexit.register(_browser_close, self)


stock.StockOnlyActiveShopClient.__init__ = b2b_client_init
stock.StockOnlyActiveShopClient._frontend_login = b2b_frontend_login

_original_get_frontend_stock = stock.StockOnlyActiveShopClient.get_frontend_stock


def browser_backed_frontend_stock(self, sku: str, data: dict[str, Any]) -> dict[str, Any]:
    """Use fast requests first; if stock is absent, read the rendered B2B DOM."""
    result = _original_get_frontend_stock(self, sku, data)
    if stock.sync.to_float(result.get("quantity")) is not None:
        return result

    errors: list[str] = []
    if result.get("error"):
        errors.append(str(result.get("error")))
    login_meta = self.frontend_stock_meta.get("_login", {})
    if isinstance(login_meta, dict) and login_meta.get("error"):
        errors.append(f"login: {login_meta.get('error')}")

    # If the original requests path could not authenticate, allow one explicit
    # browser login attempt before visiting product pages.
    if not getattr(self, "_frontend_logged_in", False):
        if not getattr(self, "_frontend_login_attempted", False):
            b2b_frontend_login(self)

    try:
        page = _ensure_browser(self)
    except Exception as error:
        errors.append(f"browser baslatilamadi: {error}")
        result["error"] = " | ".join(errors)[-3000:]
        return result

    for url in _b2b_candidates(data):
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=BROWSER_NAV_TIMEOUT_MS,
            )
            status_code = int(response.status) if response is not None else 0
            deadline = time.monotonic() + BROWSER_STOCK_WAIT_SECONDS
            quantity = None
            while time.monotonic() < deadline:
                try:
                    body_text = page.locator("body").inner_text(timeout=5000)
                except Exception:
                    body_text = ""
                quantity = stock.parse_auf_lager_stock(body_text)
                if quantity is not None:
                    break
                page.wait_for_timeout(500)

            final_url = str(page.url or url)
            if quantity is not None:
                copied = _copy_browser_cookies_to_requests(self)
                self._frontend_logged_in = True
                stock.sync.log(
                    f"B2B BROWSER STOCK | SKU {sku} | Auf Lager={quantity:g} | "
                    f"url={final_url} | cookies={copied}"
                )
                return {
                    "quantity": quantity,
                    "url": final_url,
                    "status": "FRONTEND_AUF_LAGER_BROWSER",
                    "attempts": int(result.get("attempts", 0) or 0) + 1,
                    "error": "",
                }

            errors.append(
                f"browser {final_url}: HTTP {status_code}, Auf Lager bulunamadi"
            )
        except Exception as error:
            errors.append(f"browser {url}: {type(error).__name__}: {error}")

    result["status"] = "FRONTEND_STOCK_MISSING"
    result["error"] = " | ".join(errors)[-3000:] or "Auf Lager bilgisi bulunamadi"
    return result


stock.StockOnlyActiveShopClient.get_frontend_stock = browser_backed_frontend_stock


# Do not consume 200 SKUs when the storefront stock source is systematically
# unavailable. Keep the current SKU so the next run can retry it after a fix.
_base_stock_process_one = stock.sync.process_one


def guarded_process_one(source, previous, cycle, active, plenty):
    row, stop = _base_stock_process_one(source, previous, cycle, active, plenty)
    if (
        not stop
        and row.get("overall_status") == "SOURCE_ERROR"
        and row.get("source_status") == "FRONTEND_STOCK_MISSING"
    ):
        stock.sync.log(
            f"STOK KAYNAK KRITIK HATA | SKU {source.get('input_sku', '')} | "
            "run bu SKU atlanmadan durduruluyor"
        )
        return row, True
    return row, stop


stock.sync.process_one = guarded_process_one


if __name__ == "__main__":
    raise SystemExit(stock.sync.main())
