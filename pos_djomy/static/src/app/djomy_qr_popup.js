/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * Popup d'attente d'un paiement Djomy. Avec QR (note imprimee) ou sans (le
 * client confirme sur son telephone) : dans les deux cas la caisse interroge
 * Djomy en boucle et ferme la popup a la conclusion.
 */
export class DjomyQRPopup extends Component {
    static template = "pos_djomy.DjomyQRPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        paymentLink: { type: [String, { value: null }], optional: true },
        qrCodeBase64: { type: [String, { value: null }], optional: true },
        amount: { type: Number },
        currency: { type: Object },
        instructions: { type: String, optional: true },
        expectQR: { type: Boolean, optional: true },
        progress: { type: Object, optional: true },
        onAcceptPartial: { type: Function, optional: true },
        onCancel: Function,
        close: Function,
    };
    static defaultProps = {
        title: _t("Scannez le QR Code"),
        paymentLink: null,
        qrCodeBase64: null,
        instructions: "",
        expectQR: true,
    };

    setup() {
        this.state = useState({
            status: "waiting", // waiting, success, error
            message: _t("En attente du paiement..."),
        });
        // Note partagee : avancement des paiements recus (objet reactif du terminal)
        this.progress = this.props.progress ? useState(this.props.progress) : null;
    }

    cancel() {
        this.props.onCancel();
        this.props.close();
    }

    acceptPartial() {
        if (this.props.onAcceptPartial) {
            this.props.onAcceptPartial();
        }
        this.props.close();
    }

    get canAcceptPartial() {
        return !!this.progress && this.progress.received > 0 && this.progress.received < this.progress.expected;
    }

    get progressText() {
        if (!this.progress) {
            return "";
        }
        const fmt = (v) => `${Number(v).toLocaleString()} ${this.props.currency.symbol}`;
        return _t("Reçu : %s sur %s (%s paiement(s))", fmt(this.progress.received), fmt(this.progress.expected), this.progress.count);
    }

    get formattedAmount() {
        return `${this.props.amount.toLocaleString()} ${this.props.currency.symbol}`;
    }

    get hasQRCode() {
        return !!this.props.qrCodeBase64;
    }

    get instructions() {
        if (this.props.instructions) {
            return this.props.instructions;
        }
        return _t("Le client doit scanner ce QR code avec son téléphone pour payer.");
    }

    updateStatus(status, message) {
        this.state.status = status;
        if (message) {
            this.state.message = message;
        }
    }
}
