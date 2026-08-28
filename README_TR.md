# ActiveShop -> Plenty Sync V8

V8 ile **stok ve fiyat senkronizasyonu ayrildi**. Eski yapida 3.517 SKU tek ilerleme dosyasi ile, kosu basina 300 SKU ve gunde 6 kosu isleniyordu. Bu nedenle ayni SKU'nun stok kontrolune tekrar gelmesi yaklasik 2 gun surebiliyordu.

## Stok akisi

Workflow: `.github/workflows/activeshop-plenty-stock-sync.yml`

- Her saat calisir.
- Her kosuda 200 SKU kontrol eder; teorik kapasite 4.800 SKU/gun.
- 3.517 SKU'luk tam stok turu normal kosullarda yaklasik 18 saatte tamamlanir.
- `run_stock_sync.py` fiyat yazimlarini kod seviyesinde kapatir.
- ActiveShop stogu `V1/catalogProducts/{SKU}` cevabindaki `extension_attributes.stock_qty` alanindan okunur.
- ActiveShop hedef stogu iki Plenty deposuna ayni miktarda uygulanir:
  - Global Lager = warehouse ID `1`
  - Active Shop = warehouse ID `2`
- Plenty mevcut fiziksel stok degeri correction oncesinde `/rest/stockmanagement/stock?variationId=...` rotasindan okunur.
- Hedef ile mevcut stok arasindaki fark `stock/correction` rotasina yazilir. Ornek: 57 -> 54 icin correction `-3`; 0 -> 54 icin correction `+54`.
- Correction basarili HTTP cevabi verdikten sonra stok tekrar okunmaz ve dogrulama yapilmaz; siradaki SKU'ya gecilir.
- `-3`, `+54` gibi degerler stok seviyesi degil stok hareketi/correction miktaridir.
- Ayri `stockItems`, `salable` ve `source_items` endpoint'leri bu customer token icin 401/route-not-found verdigi icin stok runner bunlari kullanmaz.
- Stok state/raporlari fiyat akisi ile karismaz:
  - `state/stock_sync_progress.json`
  - `output/stock_sync.csv`
  - `output/stock_failed_products.csv`
  - `output/stock_run_summary.json`

Yeni stok turu SKU **155288** konumundan baslatilir.

## Fiyat akisi

Workflow: `.github/workflows/activeshop-plenty-sync.yml`

- Gunde 6 kez, kosu basina 300 SKU.
- `UPDATE_STOCK=false`; stoga dokunmaz.
- Purchase price ve istenirse sales price guncellenir.
- Ayri state/raporlar kullanir:
  - `state/price_sync_progress.json`
  - `output/price_sync.csv`
  - `output/price_failed_products.csv`
  - `output/price_run_summary.json`

## SKU 155288

ActiveShop hedefi `54` ise sistem correction oncesinde Plenty fiziksel stoklarini okur. Ornek olarak Active Shop `57` ise `-3`, Global Lager `0` ise `+54` correction yazar. Yazim basarili olduktan sonra ek kontrol yapmadan siradaki SKU'ya gecer.

## Gerekli GitHub Secrets

- `ACTIVESHOP_USERNAME`
- `ACTIVESHOP_PASSWORD`
- `ACTIVESHOP_PROXY_URL` (opsiyonel)
- `PLENTY_BASE_URL`
- `PLENTY_USERNAME`
- `PLENTY_PASSWORD`

## Ana GitHub Variables

- `PLENTY_ENABLE_WRITE`
- `PLENTY_STORAGE_LOCATION_ID`
- `PLENTY_GLOBAL_STORAGE_LOCATION_ID` (opsiyonel; yoksa mevcut storage location ID kullanilir)
- `STOCK_SAFETY_DEDUCTION` (varsayilan 0)
- `STOCK_MAXIMUM` (varsayilan 999999)

## Test

```bash
python -m unittest discover -s tests -v
```

Stok konfigurasyon kontrolu:

```bash
python run_stock_sync.py --validate-only
```

Fiyat konfigurasyon kontrolu:

```bash
python run_sync.py --validate-only
```
