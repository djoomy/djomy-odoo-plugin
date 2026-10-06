/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * Liste des paiements recus par le marchand (tous liens confondus), pour
 * en rattacher un a la commande en cours.
 */
export class DjomyMatchPopup extends Component {
    static template = "pos_djomy.DjomyMatchPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        expectedAmount: { type: Number },
        currency: { type: Object },
        loadPayments: Function,
        getPayload: Function,
        close: Function,
    };
    static defaultProps = {
        title: _t("Rattacher un paiement Djomy"),
    };

    setup() {
        this.state = useState({
            loading: true,
            error: "",
            payments: [],
            selected: null,
            hours: 24,
        });
        onWillStart(() => this.refresh());
    }

    async refresh() {
        this.state.loading = true;
        this.state.error = "";
        this.state.selected = null;
        try {
            const result = await this.props.loadPayments(this.state.hours);
            if (!result.success) {
                this.state.error = result.error || _t("Djomy n'a pas répondu.");
                this.state.payments = [];
            } else {
                this.state.payments = result.payments || [];
            }
        } catch (error) {
            this.state.error = String(error);
            this.state.payments = [];
        } finally {
            this.state.loading = false;
        }
    }

    setHours(ev) {
        this.state.hours = parseInt(ev.target.value, 10) || 24;
        this.refresh();
    }

    select(payment) {
        if (!payment.isDone || payment.matchedOn) {
            return;
        }
        this.state.selected = payment.transactionId;
    }

    isSelectable(payment) {
        return payment.isDone && !payment.matchedOn;
    }

    matchesAmount(payment) {
        return Math.abs(payment.paidAmount - this.props.expectedAmount) < 0.005;
    }

    formatAmount(amount) {
        return `${Number(amount).toLocaleString()} ${this.props.currency.symbol}`;
    }

    confirm() {
        const payment = this.state.payments.find((p) => p.transactionId === this.state.selected);
        if (!payment) {
            return;
        }
        this.props.getPayload(payment);
        this.props.close();
    }

    cancel() {
        this.props.close();
    }
}
