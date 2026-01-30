# POS Djomy

Point of Sale module for mobile money payments via [Djomy](https://djomy.africa) (Orange Money, MTN MoMo, Kulu).

## Features

- QR code payment flow
- Optional SMS with payment link
- Automatic payment status polling
- Support for Orange Money, MTN Mobile Money, Kulu

## Installation

### Prerequisites

```bash
pip install qrcode[pil]
```

### Install Module

1. Install `payment_djomy` first (required dependency)
2. Install `pos_djomy`
3. Configure a POS payment method

## Configuration

### 1. Configure Djomy Provider

See configuration in the [payment_djomy](../payment_djomy/) module.

### 2. Create POS Payment Method

1. Go to **Point of Sale** > **Configuration** > **Payment Methods**
2. Create a new payment method:
   - **Name**: Djomy (or your preferred name)
   - **Payment Terminal**: Djomy
   - **Djomy Method**: Orange Money / MTN / Kulu
3. Add the payment method to your POS configuration

## Payment Flow

```
1. Cashier selects "Djomy" payment
          ↓
2. Popup: enter amount + phone (optional)
          ↓
3. QR code displayed (+ SMS if phone provided)
          ↓
4. Customer scans and pays
          ↓
5. Polling checks status (every 3s)
          ↓
6. Payment confirmed automatically
```

## Module Structure

```
pos_djomy/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   └── pos_payment_method.py   # Payment method + API integration
├── views/
│   └── pos_payment_method_views.xml
└── static/
    ├── description/
    │   └── icon.png
    └── src/app/
        ├── payment_djomy.js        # Payment interface
        ├── djomy_payment_popup.js  # Amount popup
        ├── djomy_payment_popup.xml
        ├── djomy_qr_popup.js       # QR code popup
        └── djomy_qr_popup.xml
```

## Parameters

| Parameter | Value |
|-----------|-------|
| Polling interval | 3 seconds |
| Payment timeout | 2 minutes |
| Link expiration | 15 minutes |

## API Methods

```python
# Create payment link with QR code
pos.payment.method.djomy_create_payment_link(
    payment_method_id, amount, reference, phone_number=None
)

# Check payment link status
pos.payment.method.djomy_check_link_status(payment_link_reference)

# Create direct payment (with phone number)
pos.payment.method.djomy_create_payment(
    payment_method_id, amount, phone_number, reference, djomy_method
)
```

## Dependencies

- `point_of_sale` (Odoo core)
- `payment_djomy` (this repo)
- `qrcode` (Python package)

## License

LGPL-3.0

## Author

[Dookonect](https://dookonect.com) for [Djomy](https://djomy.africa)