# ActiveShop B2B stock login secrets

The stock workflow uses two independent ActiveShop credential sets:

- `ACTIVESHOP_USERNAME` / `ACTIVESHOP_PASSWORD`: REST/API account.
- `ACTIVESHOP_B2B_USERNAME` / `ACTIVESHOP_B2B_PASSWORD`: browser login for the B2B storefront where `Auf Lager` is visible.

Do not reuse the API credentials for the B2B browser login unless the same account is explicitly enabled for both.
