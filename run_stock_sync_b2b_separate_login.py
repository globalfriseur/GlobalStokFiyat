from __future__ import annotations

import os

import run_stock_sync_b2b as b2b


B2B_USERNAME = os.getenv("ACTIVESHOP_B2B_USERNAME", "").strip()
B2B_PASSWORD = os.getenv("ACTIVESHOP_B2B_PASSWORD", "")


def separate_b2b_login(self) -> bool:
    """Use dedicated storefront credentials without touching REST API credentials."""
    if getattr(self, "_frontend_login_attempted", False):
        return bool(getattr(self, "_frontend_logged_in", False))
    self._frontend_login_attempted = True

    login_url = f"{b2b.stock.ACTIVESHOP_FRONTEND_BASE_URL}/customerb2b/account/login/"

    if not B2B_USERNAME or not B2B_PASSWORD:
        self._frontend_logged_in = False
        self.frontend_stock_meta["_login"] = {
            "status": "BROWSER_LOGIN_CONFIG_MISSING",
            "url": login_url,
            "cookies": 0,
            "error": "ACTIVESHOP_B2B_USERNAME / ACTIVESHOP_B2B_PASSWORD secret eksik",
        }
        b2b.stock.sync.log(
            "B2B LOGIN HATA | ACTIVESHOP_B2B_USERNAME / ACTIVESHOP_B2B_PASSWORD secret eksik"
        )
        return False

    try:
        page = b2b._ensure_browser(self)
        response = page.goto(
            login_url,
            wait_until="domcontentloaded",
            timeout=b2b.BROWSER_NAV_TIMEOUT_MS,
        )
        status_code = int(response.status) if response is not None else 0
        form = b2b._choose_b2b_login_form(page)
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

        email.fill(B2B_USERNAME)
        password.fill(B2B_PASSWORD)

        submit = form.locator("button[type='submit'], input[type='submit']").first
        if submit.count() == 0:
            password.press("Enter")
        else:
            submit.click()

        try:
            page.wait_for_load_state("domcontentloaded", timeout=b2b.BROWSER_NAV_TIMEOUT_MS)
        except Exception:
            pass
        page.wait_for_timeout(1200)

        final_url = str(page.url or "")
        lower_url = final_url.lower()
        still_on_login = (
            "customerb2b/account/login" in lower_url
            or "customer/account/login" in lower_url
        )
        copied = b2b._copy_browser_cookies_to_requests(self)
        self._frontend_logged_in = not still_on_login and copied > 0
        self.frontend_stock_meta["_login"] = {
            "status": "BROWSER_LOGIN_OK" if self._frontend_logged_in else "BROWSER_LOGIN_FAILED",
            "url": final_url,
            "cookies": copied,
            "error": "" if self._frontend_logged_in else "Login sayfasindan cikilamadi",
        }
        b2b.stock.sync.log(
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
        b2b.stock.sync.log(f"B2B LOGIN HATA | {type(error).__name__}: {error}")
        return False


def plenty_set_stock_for_warehouse(
    self,
    variation_id: int,
    target_quantity: float,
    warehouse_id: int,
    storage_location_id: int = 0,
) -> None:
    """Set absolute Plenty stock using the StockCorrections request envelope."""
    target_float = float(target_quantity)
    target_int = int(round(target_float))
    if abs(target_float - target_int) > 0.0001:
        raise RuntimeError(
            f"Plenty stok quantity tam sayi olmali: target={target_quantity}"
        )

    correction = {
        "variationId": int(variation_id),
        "reasonId": int(b2b.stock.sync.STOCK_CORRECTION_REASON_ID),
        "quantity": target_int,
        "storageLocationId": int(storage_location_id),
    }
    payload = {"corrections": [correction]}

    b2b.stock.sync.log(
        "PLENTY STOCK WRITE | "
        f"warehouse={int(warehouse_id)} | variation={int(variation_id)} | "
        f"target={target_int} | storageLocation={int(storage_location_id)}"
    )

    response = self.request(
        "PUT",
        f"/rest/stockmanagement/warehouses/{int(warehouse_id)}/stock/correction",
        json_body=payload,
        is_write=True,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(
            "Plenty stok guncellenemedi "
            f"(warehouse={warehouse_id}, target={target_int}): "
            f"HTTP {response.status_code} - "
            f"{b2b.stock.sync.response_error_text(response)}"
        )


# browser_backed_frontend_stock() resolves this module-global function at runtime.
b2b.b2b_frontend_login = separate_b2b_login
b2b.stock.StockOnlyActiveShopClient._frontend_login = separate_b2b_login

# Plenty correctStock expects {"corrections": [{...}]} rather than a bare entry.
b2b.stock.MultiWarehousePlentyClient.set_stock_for_warehouse = plenty_set_stock_for_warehouse


if __name__ == "__main__":
    raise SystemExit(b2b.stock.sync.main())
