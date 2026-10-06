/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { reactive } from "@odoo/owl";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { DjomyAmountPopup } from "@pos_djomy/app/djomy_payment_popup";
import { DjomyQRPopup } from "@pos_djomy/app/djomy_qr_popup";
import { DjomyOtpPopup } from "@pos_djomy/app/djomy_otp_popup";
import { DjomyMatchPopup } from "@pos_djomy/app/djomy_match_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";

const POLLING_INTERVAL = 3000; // 3 s
const PAYMENT_TIMEOUT = 180000; // 3 min : le temps d'un OTP et d'une confirmation

/**
 * Terminal de paiement "Djomy" du POS. Trois parcours :
 *
 *  - `direct` : le caissier choisit l'operateur du client et saisit son
 *               numero ; Djomy pousse une demande sur le telephone (OM/MOMO)
 *               ou envoie un OTP au client (PayCard) que le caissier saisit ;
 *  - `bill`   : une note avec QR a ete imprimee, la caisse attend les
 *               paiements de ce lien : plusieurs convives peuvent payer leur
 *               part, une ligne de paiement par paiement recu (propose
 *               seulement dans ce cas) ;
 *  - `match`  : le client a deja paye (QR statique de la boutique, QR de la
 *               note scanne apres coup) : on choisit son paiement dans la
 *               liste de ceux recus par le marchand et on le rattache.
 *
 * Tout appel Djomy passe par `pos.payment.method` cote serveur : le JS ne
 * connait ni l'API ni ses secrets.
 */
export class PaymentDjomy extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.pollingInterval = null;
        this.qrPopupClose = null;
    }

    // --- Entree ------------------------------------------------------------

    async sendPaymentRequest(uuid) {
        await super.sendPaymentRequest(...arguments);
        const order = this.pos.getOrder();
        const line = order.payment_ids.find((p) => p.uuid === uuid) || order.getSelectedPaymentline();

        const details = await this._getPaymentDetails(line, order);
        if (!details) {
            line.setPaymentStatus("retry");
            return false;
        }
        if (details.mode !== "match" && details.amount !== line.amount) {
            line.amount = details.amount;
        }
        line.setPaymentStatus("waiting");

        try {
            switch (details.mode) {
                case "bill":
                    return await this._payWithBillLink(order, line, details);
                case "match":
                    return await this._matchExistingPayment(order, line);
                default:
                    return await this._payDirect(order, line, details);
            }
        } catch (error) {
            this._showError(String(error));
            line.setPaymentStatus("retry");
            return false;
        }
    }

    async sendPaymentCancel(order, uuid) {
        super.sendPaymentCancel(...arguments);
        this._stopPolling();
        this._closeWaitingPopup();
        const line = order.payment_ids.find((p) => p.uuid === uuid) || order.getSelectedPaymentline();
        line.setPaymentStatus("retry");
        return true;
    }

    // --- Popup d'entree ----------------------------------------------------

    get djomyMethods() {
        try {
            return JSON.parse(this.payment_method_id.djomy_methods_json || "[]");
        } catch {
            return [];
        }
    }

    _availableModes(order) {
        const modes = [];
        if (order.uiState?.djomyBill) {
            modes.push({ code: "bill", label: _t("QR de la note"), icon: "fa-file-text-o" });
        }
        modes.push({ code: "direct", label: _t("Encaissement direct"), icon: "fa-mobile" });
        modes.push({ code: "match", label: _t("Déjà payé (QR / lien Djomy)"), icon: "fa-link" });
        return modes;
    }

    async _getPaymentDetails(line, order) {
        const defaultPhone = order.partner?.phone || order.partner?.mobile || "";
        const modes = this._availableModes(order);
        const bill = order.uiState?.djomyBill || null;
        const result = await makeAwaitable(this.env.services.dialog, DjomyAmountPopup, {
            title: _t("Paiement Djomy"),
            defaultAmount: line.amount,
            defaultPhoneNumber: defaultPhone,
            currency: this.pos.currency,
            modes,
            defaultMode: bill ? "bill" : modes[0].code,
            methods: this.djomyMethods,
            defaultMethod: this.payment_method_id.djomy_payment_method,
            billInfo: bill,
            onAmountChange: (amount) => {
                line.amount = amount;
            },
        });
        return result || null;
    }

    _reference(order) {
        // `name` vaut "/" tant que la commande n'est pas synchronisee
        const name = order.name && order.name !== "/" ? order.name : null;
        return order.pos_reference || name || order.uuid || `POS-${Date.now()}`;
    }

    _rpc(method, args) {
        return this.pos.data.silentCall("pos.payment.method", method, args);
    }

    // --- Parcours : QR imprime sur la note (note partagee) -----------------

    /**
     * Le lien de la note est a usage multiple : chaque convive paie sa part.
     * A chaque interrogation, les paiements aboutis non encore encaisses
     * deviennent des lignes de paiement (la premiere reutilise la ligne
     * ouverte par le caissier), et l'attente se termine quand la somme couvre
     * le montant attendu. Le caissier peut aussi encaisser ce qui est deja
     * recu et completer autrement.
     */
    async _payWithBillLink(order, line, details) {
        const bill = order.uiState?.djomyBill;
        if (!bill) {
            return await this._payDirect(order, line, details);
        }
        line.payment_ref_no = bill.reference;
        line.setPaymentStatus("waitingCard");
        const expected = this._amountDue(order, line);
        const progress = reactive({ expected, received: 0, count: 0, overpaid: 0 });
        const seen = new Set(order.payment_ids.map((p) => p.transaction_id).filter(Boolean));

        return new Promise((resolve) => {
            let settled = false;
            const finish = (success, message) => {
                if (settled) {
                    return;
                }
                settled = true;
                this._stopPolling();
                this._closeWaitingPopup();
                if (progress.overpaid > 0) {
                    this._showError(
                        _t(
                            "Les clients ont payé %s de plus que la note. Le trop-perçu reste chez Djomy : à traiter manuellement.",
                            this._formatAmount(progress.overpaid)
                        ),
                        _t("Trop-perçu Djomy")
                    );
                }
                if (success || line.transaction_id) {
                    line.setPaymentStatus("done");
                    resolve(true);
                    return;
                }
                if (message) {
                    this._showError(message);
                }
                line.setPaymentStatus("retry");
                resolve(false);
            };
            this.qrPopupClose = this.env.services.dialog.add(DjomyQRPopup, {
                title: _t("QR imprimé sur la note"),
                amount: expected,
                currency: this.pos.currency,
                paymentLink: bill.url,
                qrCodeBase64: bill.qr,
                instructions: _t("Les clients scannent le QR imprimé sur la note ; chacun peut payer sa part."),
                progress,
                onAcceptPartial: () => finish(true),
                onCancel: () => finish(false),
            });
            const check = async () => {
                const status = await this._rpc("djomy_check_link_status", [bill.reference, expected]);
                if (status?.success) {
                    this._syncBillPayments(order, line, bill, status.receivedPayments || [], seen, progress);
                }
                return status;
            };
            this._startPolling(line, check, (success) => finish(success));
        });
    }

    /**
     * Reflete les paiements aboutis du lien sur la commande : un paiement =
     * une ligne portant son `transactionId`. Un paiement qui depasse ce qui
     * reste a encaisser est plafonne et l'excedent signale.
     */
    _syncBillPayments(order, line, bill, payments, seen, progress) {
        for (const payment of payments) {
            if (!payment.transactionId || seen.has(payment.transactionId)) {
                continue;
            }
            seen.add(payment.transactionId);
            const room = Math.max(0, progress.expected - progress.received);
            const amount = Math.min(payment.paidAmount, room);
            progress.received += amount;
            progress.overpaid += payment.paidAmount - amount;
            progress.count += 1;
            if (amount <= 0) {
                continue;
            }
            let target = line;
            if (line.transaction_id) {
                target = this.pos.models["pos.payment"].create({
                    pos_order_id: order,
                    payment_method_id: this.payment_method_id,
                });
            }
            target.setAmount(amount);
            target.transaction_id = payment.transactionId;
            target.payment_method_payment_mode = payment.paymentMethod;
            target.payment_ref_no = bill.reference;
            if (target !== line) {
                target.setPaymentStatus("done");
            }
        }
    }

    _formatAmount(amount) {
        return `${Number(amount).toLocaleString()} ${this.pos.currency.symbol}`;
    }

    /**
     * Ce que cette ligne doit couvrir : le restant du de la commande, sans
     * compter la ligne elle-meme. `remainingDue` ne deduit que les lignes
     * `done` : une ligne encore en attente n'y est pas, il ne faut donc pas
     * la rajouter (sinon le montant attendu double).
     */
    _amountDue(order, line) {
        return order.remainingDue + (line.isDone() ? line.amount : 0);
    }

    // --- Parcours : encaissement direct -----------------------------------

    async _payDirect(order, line, details) {
        const response = await this._rpc("djomy_create_payment", [
            this.payment_method_id.id,
            line.amount,
            details.phoneNumber,
            this._reference(order),
            details.method,
        ]);
        if (!response.success) {
            this._showError(response.error || _t("Échec de l'initiation du paiement"));
            line.setPaymentStatus("retry");
            return false;
        }
        line.transaction_id = response.transactionId;
        line.payment_method_payment_mode = response.paymentMethod;
        const method = this.djomyMethods.find((m) => m.code === response.paymentMethod);
        const label = method?.label || response.paymentMethod;

        if (response.isDone) {
            line.setPaymentStatus("done");
            return true;
        }
        line.setPaymentStatus("waitingCard");
        const check = () => this._rpc("djomy_check_payment_status", [response.transactionId]);
        if (response.otpRequired) {
            return await this._payWithOtp(line, response, label, check);
        }
        const redirect = !!response.redirectUrl;
        return await this._waitForPayment(line, {
            title: redirect ? _t("Ouvrir %s", label) : _t("Confirmation sur le téléphone"),
            paymentLink: response.redirectUrl,
            qrCodeBase64: response.qrCodeBase64,
            expectQR: redirect,
            instructions: redirect
                ? _t("Le client scanne pour ouvrir %s et confirmer le paiement.", label)
                : _t("Le client confirme le paiement %s sur son téléphone.", label),
            check,
        });
    }

    /**
     * PayCard : le client recoit un OTP, le caissier le saisit. On interroge
     * Djomy en parallele de la saisie : si le paiement aboutit sans OTP (ou
     * pendant la saisie), la popup se ferme d'elle-meme et la ligne passe
     * `done`. Un OTP refuse par Djomy rouvre la saisie avec le motif.
     */
    _payWithOtp(line, response, label, check) {
        return new Promise((resolve) => {
            let settled = false;
            const finish = (success, message) => {
                if (settled) {
                    return;
                }
                settled = true;
                this._closeWaitingPopup();
                this._finish(line, resolve, success, message);
            };
            const askPin = (error = "") => {
                this._closeWaitingPopup();
                this.qrPopupClose = this.env.services.dialog.add(DjomyOtpPopup, {
                    amount: line.amount,
                    currency: this.pos.currency,
                    methodLabel: label,
                    error,
                    getPayload: async ({ pin }) => {
                        const result = await this._rpc("djomy_confirm_otp", [response.transactionId, pin]);
                        if (settled) {
                            return;
                        }
                        if (result.success && result.isDone) {
                            finish(true);
                        } else if (result.success && !result.isFailed) {
                            // OTP accepte : la confirmation arrive par le polling
                            this._closeWaitingPopup();
                            this.qrPopupClose = this.env.services.dialog.add(DjomyQRPopup, {
                                title: _t("Confirmation %s", label),
                                amount: line.amount,
                                currency: this.pos.currency,
                                expectQR: false,
                                instructions: _t("Code accepté, confirmation du paiement en cours."),
                                onCancel: () => finish(false),
                            });
                        } else {
                            askPin(result.error || _t("Code refusé par Djomy, réessayez."));
                        }
                    },
                    onCancel: () => finish(false),
                });
            };
            askPin();
            this._startPolling(line, check, (success) => finish(success));
        });
    }

    // --- Parcours : rattacher un paiement deja recu -------------------------

    async _matchExistingPayment(order, line) {
        const payment = await makeAwaitable(this.env.services.dialog, DjomyMatchPopup, {
            expectedAmount: line.amount,
            currency: this.pos.currency,
            loadPayments: (hours) =>
                this._rpc("djomy_list_recent_payments", [this.payment_method_id.id, hours, this.pos.config.id]),
        });
        if (!payment) {
            line.setPaymentStatus("retry");
            return false;
        }
        const verify = await this._rpc("djomy_verify_payment", [payment.transactionId]);
        if (!verify.success) {
            this._showError(verify.error || _t("Djomy n'a pas confirmé ce paiement."));
            line.setPaymentStatus("retry");
            return false;
        }
        if (!verify.isDone) {
            this._showError(_t("Ce paiement n'est pas abouti chez Djomy (statut %s).", verify.status));
            line.setPaymentStatus("retry");
            return false;
        }
        if (verify.matchedOn) {
            this._showError(_t("Ce paiement est déjà encaissé sur %s.", verify.matchedOn));
            line.setPaymentStatus("retry");
            return false;
        }
        const maxAmount = this._amountDue(order, line);
        if (verify.paidAmount > maxAmount + 0.005) {
            this._showError(
                _t(
                    "Le paiement (%s) dépasse le restant dû de la commande (%s). Rattachez-le depuis le back-office après correction.",
                    verify.paidAmount,
                    maxAmount
                )
            );
            line.setPaymentStatus("retry");
            return false;
        }
        if (verify.paidAmount !== line.amount) {
            line.amount = verify.paidAmount;
        }
        line.transaction_id = verify.transactionId;
        line.payment_method_payment_mode = verify.paymentMethod;
        line.payment_ref_no = payment.linkReference || payment.merchantReference || _t("Paiement Djomy");
        line.setPaymentStatus("done");
        return true;
    }

    // --- Attente et polling ------------------------------------------------

    _waitForPayment(line, { check, ...popupProps }) {
        return new Promise((resolve) => {
            this.qrPopupClose = this.env.services.dialog.add(DjomyQRPopup, {
                amount: line.amount,
                currency: this.pos.currency,
                ...popupProps,
                onCancel: () => {
                    this._stopPolling();
                    this.qrPopupClose = null;
                    line.setPaymentStatus("retry");
                    resolve(false);
                },
            });
            this._startPolling(line, check, resolve);
        });
    }

    _startPolling(line, check, resolve) {
        let elapsedTime = 0;
        this._stopPolling();

        this.pollingInterval = setInterval(async () => {
            elapsedTime += POLLING_INTERVAL;

            if (elapsedTime >= PAYMENT_TIMEOUT) {
                this._finish(line, resolve, false, _t("Délai expiré. Le client n'a pas complété le paiement."));
                return;
            }

            try {
                const status = await check();
                if (!status || !status.success) {
                    return; // Djomy injoignable : on retente au prochain tour
                }
                if (status.isDone) {
                    if (status.transactionId && !line.transaction_id) {
                        line.transaction_id = status.transactionId;
                    }
                    if (status.paymentMethod && !line.payment_method_payment_mode) {
                        line.payment_method_payment_mode = status.paymentMethod;
                    }
                    this._finish(line, resolve, true);
                    return;
                }
                if (status.isFailed || status.isCancelled || status.isExpired) {
                    const message = status.isExpired
                        ? _t("Le lien de paiement a expiré.")
                        : status.isCancelled
                          ? _t("Paiement annulé par le client")
                          : _t("Le paiement a échoué");
                    this._finish(line, resolve, false, message);
                    return;
                }
                line.setPaymentStatus("waitingCard");
            } catch (error) {
                console.error("Djomy: error checking payment status:", error);
            }
        }, POLLING_INTERVAL);
    }

    _finish(line, resolve, success, errorMessage) {
        this._stopPolling();
        this._closeWaitingPopup();
        if (success) {
            line.setPaymentStatus("done");
        } else {
            if (errorMessage) {
                this._showError(errorMessage);
            }
            line.setPaymentStatus("retry");
        }
        resolve(success);
    }

    _closeWaitingPopup() {
        if (this.qrPopupClose) {
            this.qrPopupClose();
            this.qrPopupClose = null;
        }
    }

    _stopPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
    }

    _showError(message, title) {
        this.env.services.dialog.add(AlertDialog, {
            title: title || _t("Erreur Paiement Djomy"),
            body: message,
        });
    }
}

register_payment_method("djomy", PaymentDjomy);
