/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { DjomyAmountPopup } from "@pos_djomy/app/djomy_payment_popup";
import { DjomyQRPopup } from "@pos_djomy/app/djomy_qr_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";

const POLLING_INTERVAL = 3000; // 3 seconds
const PAYMENT_TIMEOUT = 120000; // 2 minutes

export class PaymentDjomy extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.pollingInterval = null;
        this.paymentTimeout = null;
        this.currentPaymentLinkReference = null;
        this.qrPopupClose = null;
    }

    async sendPaymentRequest(uuid) {
        await super.sendPaymentRequest(...arguments);
        const line = this.pos.getOrder().getSelectedPaymentline();
        const order = this.pos.getOrder();

        // Step 1: Show popup for amount and optional phone number
        const paymentDetails = await this._getPaymentDetails(line.amount, order, line);
        if (!paymentDetails) {
            line.setPaymentStatus("retry");
            return false;
        }

        // Update payment line amount if changed
        if (paymentDetails.amount !== line.amount) {
            line.amount = paymentDetails.amount;
        }

        line.setPaymentStatus("waiting");

        try {
            // Step 2: Generate payment link (with optional phone for SMS)
            const reference = order.name || `POS-${Date.now()}`;

            const linkResponse = await this._createPaymentLink(
                line.amount,
                reference,
                paymentDetails.phoneNumber
            );

            if (!linkResponse.success) {
                this._showError(linkResponse.error || _t("Echec de la generation du lien de paiement"));
                line.setPaymentStatus("retry");
                return false;
            }

            // Store payment link reference for polling
            this.currentPaymentLinkReference = linkResponse.paymentLinkReference;
            line.transaction_id = this.currentPaymentLinkReference;

            // Step 3: Show QR code popup and start polling
            line.setPaymentStatus("waitingCard");
            return await this._showQRCodeAndPoll(
                linkResponse.paymentLink,
                linkResponse.qrCodeBase64,
                line,
                linkResponse.smsSent
            );

        } catch (error) {
            this._showError(String(error));
            line.setPaymentStatus("retry");
            return false;
        }
    }

    async sendPaymentCancel(order, uuid) {
        super.sendPaymentCancel(...arguments);
        this._stopPolling();

        // Close QR popup if open
        if (this.qrPopupClose) {
            this.qrPopupClose();
            this.qrPopupClose = null;
        }

        const line = this.pos.getOrder().getSelectedPaymentline();
        line.setPaymentStatus("retry");
        return true;
    }

    async _getPaymentDetails(defaultAmount, order, line) {
        // Try to get phone from partner
        let defaultPhone = "";
        if (order.partner?.phone) {
            defaultPhone = order.partner.phone;
        } else if (order.partner?.mobile) {
            defaultPhone = order.partner.mobile;
        }

        const result = await makeAwaitable(this.env.services.dialog, DjomyAmountPopup, {
            title: _t("Paiement JOMI (QR)"),
            defaultAmount: defaultAmount,
            defaultPhoneNumber: defaultPhone,
            currency: this.pos.currency,
            onAmountChange: (amount) => {
                line.amount = amount;
            },
        });

        if (!result) {
            return null;
        }

        return {
            amount: result.amount,
            phoneNumber: result.phoneNumber,
        };
    }

    async _createPaymentLink(amount, reference, phoneNumber) {
        return await this.pos.data.silentCall(
            "pos.payment.method",
            "djomy_create_payment_link",
            [this.payment_method_id.id, amount, reference, phoneNumber]
        );
    }

    async _showQRCodeAndPoll(paymentLink, qrCodeBase64, line, smsSent = false) {
        return new Promise((resolve) => {
            // Show QR popup
            this.qrPopupClose = this.env.services.dialog.add(DjomyQRPopup, {
                title: _t("Scannez le QR Code"),
                paymentLink: paymentLink,
                qrCodeBase64: qrCodeBase64,
                amount: line.amount,
                currency: this.pos.currency,
                smsSent: smsSent,
                onCancel: () => {
                    this._stopPolling();
                    line.setPaymentStatus("retry");
                    resolve(false);
                },
            });

            // Start polling for payment status
            this._startPolling(line, resolve);
        });
    }

    _startPolling(line, resolve) {
        let elapsedTime = 0;

        this.pollingInterval = setInterval(async () => {
            elapsedTime += POLLING_INTERVAL;

            // Check for timeout
            if (elapsedTime >= PAYMENT_TIMEOUT) {
                this._stopPolling();
                if (this.qrPopupClose) {
                    this.qrPopupClose();
                    this.qrPopupClose = null;
                }
                this._showError(_t("Delai expire. Le client n'a pas complete le paiement."));
                line.setPaymentStatus("retry");
                resolve(false);
                return;
            }

            try {
                const status = await this._checkPaymentStatus();

                if (status.isDone) {
                    this._stopPolling();
                    if (this.qrPopupClose) {
                        this.qrPopupClose();
                        this.qrPopupClose = null;
                    }
                    line.setPaymentStatus("done");
                    resolve(true);
                    return;
                }

                if (status.isFailed || status.isCancelled) {
                    this._stopPolling();
                    if (this.qrPopupClose) {
                        this.qrPopupClose();
                        this.qrPopupClose = null;
                    }
                    const message = status.isCancelled
                        ? _t("Paiement annule par le client")
                        : _t("Le paiement a echoue");
                    this._showError(message);
                    line.setPaymentStatus("retry");
                    resolve(false);
                    return;
                }

                // Still pending, continue polling
                line.setPaymentStatus("waitingCard");

            } catch (error) {
                console.error("Error checking payment status:", error);
                // Continue polling on error, don't fail immediately
            }
        }, POLLING_INTERVAL);
    }

    async _checkPaymentStatus() {
        if (!this.currentPaymentLinkReference) {
            return { success: false, error: "No payment link reference" };
        }

        return await this.pos.data.silentCall(
            "pos.payment.method",
            "djomy_check_link_status",
            [this.currentPaymentLinkReference]
        );
    }

    _stopPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
        if (this.paymentTimeout) {
            clearTimeout(this.paymentTimeout);
            this.paymentTimeout = null;
        }
        this.currentPaymentLinkReference = null;
    }

    _showError(message, title) {
        this.env.services.dialog.add(AlertDialog, {
            title: title || _t("Erreur Paiement Djomy"),
            body: message,
        });
    }
}

register_payment_method("djomy", PaymentDjomy);
