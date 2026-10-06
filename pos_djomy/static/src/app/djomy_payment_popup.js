/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * Popup d'entree du paiement Djomy : operateur du client (Orange Money,
 * MTN MoMo, PayCard), numero, montant.
 *
 * Parcours :
 *  - `direct` : encaissement pousse sur le telephone du client, ou OTP ;
 *  - `bill`   : attendre le QR deja imprime sur la note (si elle existe) ;
 *  - `match`  : rattacher un paiement deja recu par le marchand.
 */
export class DjomyAmountPopup extends Component {
    static template = "pos_djomy.DjomyAmountPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        defaultAmount: { type: Number },
        defaultPhoneNumber: { type: String, optional: true },
        currency: { type: Object },
        modes: { type: Array },
        defaultMode: { type: String, optional: true },
        methods: { type: Array, optional: true },
        defaultMethod: { type: String, optional: true },
        billInfo: { type: [Object, { value: null }], optional: true },
        onAmountChange: { type: Function, optional: true },
        getPayload: Function,
        close: Function,
    };
    static defaultProps = {
        title: _t("Paiement Djomy"),
        defaultPhoneNumber: "",
        methods: [],
        billInfo: null,
    };

    setup() {
        const modes = this.props.modes.map((m) => m.code);
        this.state = useState({
            amount: this.props.defaultAmount,
            phoneNumber: this.props.defaultPhoneNumber,
            mode: modes.includes(this.props.defaultMode) ? this.props.defaultMode : modes[0],
            method: this.props.defaultMethod || this.props.methods[0]?.code || "OM",
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

    setMode(code) {
        this.state.mode = code;
        if (code === "bill" && this.props.billInfo) {
            this.state.amount = this.props.billInfo.amount;
            if (this.props.onAmountChange) {
                this.props.onAmountChange(this.state.amount);
            }
        }
    }

    setMethod(ev) {
        this.state.method = ev.target.value;
    }

    get isDirect() {
        return this.state.mode === "direct";
    }

    get isBill() {
        return this.state.mode === "bill";
    }

    get isMatch() {
        return this.state.mode === "match";
    }

    get selectedMethod() {
        return this.props.methods.find((m) => m.code === this.state.method);
    }

    get isAccountNumber() {
        // PayCard : un numero de compte, pas un telephone
        return !!this.selectedMethod?.otp;
    }

    get canConfirm() {
        if (this.isMatch) {
            return true;
        }
        if (this.state.amount <= 0) {
            return false;
        }
        if (this.isDirect) {
            return !!this._formatIdentifier(this.state.phoneNumber);
        }
        return true;
    }

    get confirmLabel() {
        switch (this.state.mode) {
            case "bill":
                return _t("Attendre le paiement");
            case "match":
                return _t("Choisir le paiement");
            default:
                return _t("Envoyer la demande");
        }
    }

    confirm() {
        if (!this.canConfirm) {
            return;
        }
        this.props.getPayload({
            mode: this.state.mode,
            amount: this.state.amount,
            phoneNumber: this._formatIdentifier(this.state.phoneNumber),
            method: this.state.method,
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

    /**
     * Identifiant a envoyer : telephone normalise pour le mobile money,
     * numero de compte (chiffres) pour PayCard. Le serveur refait la meme
     * chose : ici on ne cherche qu'a valider la saisie avant envoi.
     */
    _formatIdentifier(value) {
        if (this.isAccountNumber) {
            const digits = (value || "").replace(/\D/g, "");
            return digits.length >= 6 ? digits : null;
        }
        return this._formatPhoneNumber(value);
    }

    /**
     * Normalise vers 00<indicatif><numero>.
     */
    _formatPhoneNumber(phone) {
        if (!phone) {
            return null;
        }
        let cleaned = phone.replace(/[^\d+]/g, "");
        if (cleaned.startsWith("+")) {
            cleaned = "00" + cleaned.substring(1);
        }
        if (!cleaned) {
            return null;
        }
        if (!cleaned.startsWith("00")) {
            cleaned = "00224" + cleaned;
        }
        return cleaned.length >= 11 ? cleaned : null;
    }

    get formattedAmount() {
        return `${this.state.amount} ${this.props.currency.symbol}`;
    }
}
