/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

const BILL_LINK_REUSE_MARGIN_MS = 5 * 60 * 1000;

/**
 * QR de paiement sur la note : avant d'imprimer une commande qui n'est pas
 * encore reglee, on cree un lien de paiement Djomy pour le restant du et on
 * le garde sur la commande. Le recu l'affiche ; l'ecran de paiement le
 * retrouve ensuite (parcours "QR de la note") et attend ce paiement-la.
 *
 * Un echec Djomy ne doit jamais empecher d'imprimer : on log et on imprime
 * sans QR.
 */
patch(PosStore.prototype, {
    async printReceipt({ basic = false, order = this.getOrder(), printBillActionTriggered = false } = {}) {
        await this.djomyPrepareBillLink(order);
        return super.printReceipt(...arguments);
    },

    djomyPaymentMethod() {
        return (this.config.payment_method_ids || []).find(
            (pm) => pm.use_payment_terminal === "djomy"
        );
    },

    async djomyPrepareBillLink(order) {
        try {
            if (!order || order.finalized || !(order.remainingDue > 0)) {
                return;
            }
            const method = this.djomyPaymentMethod();
            if (!method) {
                return;
            }
            const amount = order.remainingDue;
            const existing = order.uiState.djomyBill;
            if (
                existing &&
                existing.amount === amount &&
                existing.expiresAt &&
                new Date(existing.expiresAt).getTime() > Date.now() + BILL_LINK_REUSE_MARGIN_MS
            ) {
                return;
            }
            const name = order.name && order.name !== "/" ? order.name : null;
            const reference = order.pos_reference || name || order.uuid;
            const response = await this.data.silentCall(
                "pos.payment.method",
                "djomy_create_payment_link",
                [method.id, amount, reference, null, "bill"]
            );
            if (!response?.success || !response.qrCodeBase64) {
                order.uiState.djomyBill = null;
                return;
            }
            order.uiState.djomyBill = {
                reference: response.paymentLinkReference,
                url: response.paymentLink,
                qr: response.qrCodeBase64,
                amount,
                expiresAt: response.expiresAt,
            };
        } catch (error) {
            console.warn("Djomy: QR de la note non genere", error);
        }
    },
});
