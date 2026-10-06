# Djomy Odoo Plugin

Official Odoo 19 modules for [Djomy](https://djomy.africa), the West African mobile
money aggregator (Orange Money, MTN Mobile Money, Kulu, PayCard, bank cards).

| Module | Role | Where |
|---|---|---|
| [payment_djomy](./payment_djomy/) | payment provider, API client, webhook, synchronisation, manual payment matching | customer portal, eCommerce, back office |
| [pos_djomy](./pos_djomy/) | cashier checkout (operator + customer number, PayCard OTP), QR on the bill, "already paid" matching, per-shop QR | Point of Sale |

Each module has its own README (in French, the language of the merchants these
modules are built for) with the detailed configuration, flows and tests.

## What the modules do

### Portal and eCommerce (`payment_djomy`)

- **Payment link per transaction** (`POST /v1/links`, configurable validity): the
  customer is redirected to the Djomy payment page; a link sent by SMS in the morning
  is still payable in the afternoon.
- **Return and webhook** (V1 and V2 payloads, HMAC-SHA256 signature on the raw body):
  the status is always re-read from `GET /v1/payments/{id}/status` before a
  transaction is confirmed. The first to arrive wins, the other is idempotent.
- **Synchronisation**: a "Synchronise with Djomy" button on the transaction, a
  reconciliation cron (every 10 min) for lost webhooks, and a stale-pending
  cancellation cron that re-synchronises before cancelling.
- **Manual matching**: "Attach a Djomy payment" on customer invoices and POS orders.
  The wizard lists every payment received by the merchant over a period
  (`GET /v1/payments`, 100 per page, whatever the link it came from) or the payments
  of one link, re-checks the status, then books an offline transaction and its
  accounting payment. A `transactionId` can only be matched once.
- **Static QR** (optional): a multi-use, no-amount link to print and display at the
  counter.

### Point of Sale (`pos_djomy`)

- **Direct checkout**: the cashier picks the customer's operator (Orange Money, MTN
  MoMo, PayCard) and types the number. Djomy pushes a confirmation to the phone
  (OM, MoMo) or sends an OTP to the customer that the cashier types in (PayCard).
  `POST /v1/payments`, status polled every 3 s.
- **QR on the bill**: printing an unpaid order creates a multi-use link and prints its
  QR with the amount due. The bill is **shared**: several guests scan the same QR
  and each pays their share; the register sums the successful payments, books one
  payment line per Djomy payment, and lets the cashier collect what has been received
  and complete otherwise. An overpayment is capped and reported.
- **Already paid**: the customer paid a static QR, or the bill QR after another
  checkout. The cashier picks the payment among the recent ones (shop QR first), it
  is re-verified and booked at the paid amount.
- **Per-shop QR**: each POS configuration can generate its own static QR in the POS
  settings, in addition to the merchant-wide one; payments are tagged "shop QR" /
  "general QR".
- Guard rails: one `transactionId` per payment line (model constraint), already
  matched payments greyed out and refused, amounts above the remaining due refused.

## Djomy endpoints used

| Endpoint | Used for |
|---|---|
| `POST /v1/auth` | bearer token, cached with its expiry, refreshed on 401 |
| `POST /v1/links` | payment links: portal, bill QR, static QR. Multi-use links always carry a `usageLimit`: without it Djomy closes the link after the first successful payment |
| `GET /v1/links/{reference}` | link status and its `payments[]` |
| `GET /v1/payments` | every payment of the merchant over a period (Spring page: `content`, `last`, `totalPages`; `startDate`/`endDate` as dates, both or none) |
| `GET /v1/payments/{id}/status` | the official status of a payment, the only source of truth before a transaction is confirmed |
| `POST /v1/payments` | direct checkout at the counter |
| `POST /v1/payments/{ref}/confirmOTP` | PayCard OTP |

| Mode | API |
|---|---|
| Test | `https://sandbox-api.djomy.africa/v1/` |
| Enabled | `https://api.djomy.africa/v1/` |

Webhook to declare in the Djomy dashboard: `https://<your-domain>/payment/djomy/webhook`
(header `X-Webhook-Signature: v1:<HMAC-SHA256(raw body, clientSecret)>`).

## Installation

1. Copy `payment_djomy` and `pos_djomy` into your addons path. `qrcode` is required
   for the QR codes; the official Odoo Docker image already ships it.
2. Update the apps list, install **Payment Provider: Djomy**, then **POS Djomy** if
   you use the Point of Sale.
3. **Invoicing › Configuration › Payment Providers › Djomy**: Client ID, Client
   Secret, Partner Domain (required in production), link validity, allowed payment
   methods; mode Test or Enabled; publish. Optionally generate the static QR.
4. **Point of Sale › Configuration › Payment Methods**: a method with *Payment
   Terminal* = Djomy and a default operator; add it to the POS. In the POS settings
   (Payment block), optionally generate the shop QR.

System parameters: `djomy.webhook_verify_signature` (True), `djomy.pending_auto_cancel_minutes`
(120), `djomy.sync_pending_max_hours` (24), `djomy.bill_link_expiry_minutes` (180).

## Tests

```bash
odoo -d <test-db> -i payment_djomy,pos_djomy,sale --test-enable \
     --test-tags /payment_djomy,/pos_djomy --without-demo --workers=0 --stop-after-init
```

The Djomy API is simulated (`payment_djomy/tests/common.py`): no test reaches the
network.

## Requirements

Odoo 19.0, Python 3.10+. Currencies: GNF, XOF, EUR, USD.

## License

LGPL-3.0.

## Support

- Djomy API documentation: https://developers.djomy.africa
- Issues: https://github.com/djoomy/djomy-odoo-plugin/issues

Developed by [Dookonect](https://dookonect.com) for [Djomy](https://djomy.africa).
