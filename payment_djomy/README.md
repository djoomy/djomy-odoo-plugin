# Payment Provider: Djomy

Odoo 19 payment provider module for [Djomy](https://djomy.africa), a mobile money aggregator for West Africa (Guinea, Senegal, Mali, etc.).

## Features

- Accept payments via **Orange Money**, **MTN Mobile Money**, and **Kulu**
- Test (sandbox) and production modes
- Webhook notifications for real-time payment status updates
- Automatic OAuth2 token management with refresh
- HMAC signature verification for secure API calls
- Multi-currency support: GNF, XOF, EUR, USD

## Installation

1. Copy the module to your Odoo `addons` directory
2. Update the apps list
3. Install "Payment Provider: Djomy"

```bash
cp -r payment_djomy /path/to/odoo/addons/
```

## Configuration

1. Go to **Invoicing** > **Configuration** > **Payment Providers**
2. Click on **Djomy**
3. Configure:
   - **Client ID**: Your Djomy API client ID
   - **Client Secret**: Your Djomy API client secret
   - **Partner Domain** *(optional)*: Your registered domain with Djomy
4. Select mode: **Test** (sandbox) or **Enabled** (production)
5. Click **Publish** to make it available to customers

## API Endpoints

| Mode | Base URL |
|------|----------|
| Test (Sandbox) | `https://sandbox-api.djomy.africa/v1/` |
| Production | `https://api.djomy.africa/v1/` |

## Webhooks

Configure the following webhook URL in your Djomy dashboard:

```
https://yourdomain.com/payment/djomy/webhook
```

The webhook handles payment success, failure, and refund notifications.

## Payment Flow

```
1. Customer selects Djomy as payment method
2. Customer enters their phone number
3. Click "Pay" - redirected to Djomy checkout
4. Customer confirms payment on their mobile device
5. Djomy sends webhook notification
6. Customer redirected back to Odoo with payment status
```

## Module Structure

```
payment_djomy/
├── __init__.py
├── __manifest__.py
├── const.py                    # Constants (URLs, currencies, status codes)
├── controllers/
│   ├── __init__.py
│   └── main.py                 # HTTP routes (return, webhook)
├── models/
│   ├── __init__.py
│   ├── payment_provider.py     # Provider configuration & API client
│   └── payment_transaction.py  # Transaction handling
├── views/
│   ├── payment_provider_views.xml
│   └── payment_djomy_templates.xml
├── data/
│   └── payment_provider_data.xml
└── static/
    ├── description/
    │   └── icon.png
    └── src/
        ├── img/
        │   ├── djomy.png
        │   └── djomy-logo.svg
        └── js/
            └── payment_form.js
```

## Supported Currencies

| Currency | Description |
|----------|-------------|
| GNF | Guinean Franc |
| XOF | CFA Franc (West Africa) |
| EUR | Euro |
| USD | US Dollar |

## Dependencies

- `payment` (Odoo core module)

## License

LGPL-3.0

## Author

[Dookonect](https://dookonect.com) for [Djomy](https://djomy.africa)