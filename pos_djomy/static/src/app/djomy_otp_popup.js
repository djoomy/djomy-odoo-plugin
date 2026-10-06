/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * Saisie de l'OTP recu par le client (PayCard) : 4 a 6 chiffres.
 */
export class DjomyOtpPopup extends Component {
    static template = "pos_djomy.DjomyOtpPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        amount: { type: Number },
        currency: { type: Object },
        methodLabel: { type: String, optional: true },
        error: { type: String, optional: true },
        onCancel: { type: Function, optional: true },
        getPayload: Function,
        close: Function,
    };
    static defaultProps = {
        title: _t("Code de confirmation"),
        methodLabel: "",
        error: "",
    };

    setup() {
        this.state = useState({ pin: "", error: this.props.error });
        this.inputRef = useRef("pinInput");
        onMounted(() => this.inputRef.el?.focus());
    }

    onInput(ev) {
        this.state.pin = ev.target.value.replace(/\D/g, "").slice(0, 6);
        this.state.error = "";
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            this.confirm();
        }
    }

    get isValid() {
        return /^\d{4,6}$/.test(this.state.pin);
    }

    confirm() {
        if (!this.isValid) {
            this.state.error = _t("Le code doit comporter 4 à 6 chiffres.");
            return;
        }
        this.props.close();
        this.props.getPayload({ pin: this.state.pin });
    }

    cancel() {
        this.props.close();
        if (this.props.onCancel) {
            this.props.onCancel();
        }
    }

    get formattedAmount() {
        return `${this.props.amount.toLocaleString()} ${this.props.currency.symbol}`;
    }
}
