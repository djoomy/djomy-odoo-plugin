# Djomy Odoo Plugin

Official Odoo modules for integrating [Djomy](https://djomy.africa) mobile money payments.

Djomy is a payment aggregator for West Africa supporting **Orange Money**, **MTN Mobile Money**, and **Kulu**.

## Modules

| Module | Description | Odoo Version |
|--------|-------------|--------------|
| [payment_djomy](./payment_djomy/) | Payment provider for e-commerce/invoicing | 19.0 |
| [pos_djomy](./pos_djomy/) | Point of Sale integration with QR code | 19.0 |

## Features

### E-Commerce & Invoicing (`payment_djomy`)
- Online payments via Djomy checkout page
- Webhook notifications for payment status
- Automatic token management
- Multi-currency support (GNF, XOF, EUR, USD)

### Point of Sale (`pos_djomy`)
- QR code payment flow
- Optional SMS with payment link
- Real-time status polling
- Support for Orange Money, MTN MoMo, Kulu

## Installation

### Prerequisites

```bash
# Required for QR code generation (pos_djomy)
pip install qrcode[pil]
```

### Install Modules

1. Copy both modules to your Odoo `addons` directory
2. Update the apps list in Odoo
3. Install **Payment Provider: Djomy** first
4. Install **POS Djomy** if you use Point of Sale

```bash
# Example: copy to addons
cp -r payment_djomy pos_djomy /path/to/odoo/addons/
```

## Configuration

### 1. Configure Djomy Provider

1. Go to **Invoicing** > **Configuration** > **Payment Providers**
2. Click on **Djomy**
3. Enter your credentials:
   - **Client ID**: Provided by Djomy
   - **Client Secret**: Provided by Djomy
   - **Partner Domain**: Your domain registered and validated by Djomy *(optional in Test mode, required in Production)*
4. Select mode: **Test** or **Enabled**
5. Click **Publish**

### 2. Configure POS Payment Method (if using POS)

1. Go to **Point of Sale** > **Configuration** > **Payment Methods**
2. Create a new payment method:
   - **Name**: Djomy (or your preferred name)
   - **Payment Terminal**: Djomy
   - **Djomy Method**: Orange Money / MTN / Kulu
3. Add the payment method to your POS configuration

## API Environments

| Mode | API URL |
|------|---------|
| Test (Sandbox) | `https://sandbox-api.djomy.africa/v1/` |
| Production | `https://api.djomy.africa/v1/` |

## Webhooks

Configure the webhook URL in your Djomy dashboard:

```
https://yourdomain.com/payment/djomy/webhook
```

## Payment Flows

### E-Commerce Flow

```
Customer selects Djomy → Enters phone number → Redirected to Djomy
    → Confirms on mobile → Webhook notification → Order confirmed
```

### POS Flow

```
Cashier selects Djomy → Enters amount → QR code displayed
    → Customer scans → Pays on mobile → Auto-confirmed via polling
```

## Supported Currencies

- **GNF** - Guinean Franc
- **XOF** - CFA Franc (West Africa)
- **EUR** - Euro
- **USD** - US Dollar

## Directory Structure

```
djomy-odoo-plugin/
├── README.md                 # This file
├── payment_djomy/            # E-commerce payment provider
│   ├── __manifest__.py
│   ├── const.py              # API URLs, currencies, status codes
│   ├── controllers/          # HTTP routes (webhooks)
│   ├── models/               # Payment provider & transactions
│   ├── views/                # UI templates
│   ├── data/                 # Default provider data
│   └── static/               # Assets (JS, images)
└── pos_djomy/                # Point of Sale integration
    ├── __manifest__.py
    ├── models/               # POS payment method
    ├── views/                # Configuration views
    └── static/               # POS UI components (JS, XML)
```

## Requirements

- **Odoo**: 19.0
- **Python**: 3.10+
- **Dependencies**: `qrcode[pil]` (for POS module)

## License

LGPL-3.0 - See [LICENSE](https://www.gnu.org/licenses/lgpl-3.0.html)

## Support

- **Djomy API Documentation**: [https://developers.djomy.africa](https://developers.djomy.africa)
- **Issues**: [GitHub Issues](https://github.com/djoomy/djomy-odoo-plugin/issues)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

---

Developed by [Dookonect](https://dookonect.com) for [Djomy](https://djomy.africa)