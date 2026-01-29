/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class DjomyQRPopup extends Component {
    static template = "pos_djomy.DjomyQRPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        paymentLink: { type: [String, { value: null }], optional: true },
        qrCodeBase64: { type: [String, { value: null }], optional: true },
        amount: { type: Number },
        currency: { type: Object },
        smsSent: { type: Boolean, optional: true },
        onCancel: Function,
        close: Function,
    };
    static defaultProps = {
        title: _t("Scannez le QR Code"),
        smsSent: false,
        paymentLink: null,
        qrCodeBase64: null,
    };

    setup() {
        this.state = useState({
            status: "waiting", // waiting, success, error
            message: _t("En attente du paiement..."),
        });
    }

    cancel() {
        this.props.onCancel();
        this.props.close();
    }

    get formattedAmount() {
        return `${this.props.amount.toLocaleString()} ${this.props.currency.symbol}`;
    }

    get hasQRCode() {
        return !!this.props.qrCodeBase64;
    }

    updateStatus(status, message) {
        this.state.status = status;
        if (message) {
            this.state.message = message;
        }
    }
}
