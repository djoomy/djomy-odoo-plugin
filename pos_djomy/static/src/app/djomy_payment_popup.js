/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class DjomyAmountPopup extends Component {
    static template = "pos_djomy.DjomyAmountPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        defaultAmount: { type: Number },
        defaultPhoneNumber: { type: String, optional: true },
        currency: { type: Object },
        onAmountChange: { type: Function, optional: true },
        getPayload: Function,
        close: Function,
    };
    static defaultProps = {
        title: _t("Paiement JOMI (QR)"),
        defaultPhoneNumber: "",
    };

    setup() {
        this.state = useState({
            amount: this.props.defaultAmount,
            phoneNumber: this.props.defaultPhoneNumber,
        });
        this.amountInputRef = useRef("amountInput");
        onMounted(() => {
            if (this.amountInputRef.el) {
                this.amountInputRef.el.focus();
                this.amountInputRef.el.select();
            }
        });
    }

    onAmountChange(ev) {
        const value = parseFloat(ev.target.value) || 0;
        this.state.amount = value;
        if (this.props.onAmountChange) {
            this.props.onAmountChange(value);
        }
    }

    onPhoneChange(ev) {
        this.state.phoneNumber = ev.target.value;
    }

    confirm() {
        if (this.state.amount <= 0) {
            return;
        }
        this.props.getPayload({
            amount: this.state.amount,
            phoneNumber: this._formatPhoneNumber(this.state.phoneNumber),
        });
        this.props.close();
    }

    cancel() {
        this.props.close();
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            this.confirm();
        }
    }

    _formatPhoneNumber(phone) {
        if (!phone) return null;

        // Remove all non-numeric characters except +
        let cleaned = phone.replace(/[^\d+]/g, "");

        // Convert + to 00
        if (cleaned.startsWith("+")) {
            cleaned = "00" + cleaned.substring(1);
        }

        // If it starts with just country code (like 224), add 00
        if (!cleaned.startsWith("00") && cleaned.length >= 9) {
            // Assume Guinea if starts with 6
            if (cleaned.startsWith("6")) {
                cleaned = "00224" + cleaned;
            }
        }

        return cleaned || null;
    }

    get formattedAmount() {
        return `${this.state.amount} ${this.props.currency.symbol}`;
    }
}
