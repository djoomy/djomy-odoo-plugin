/** @odoo-module */

import { _t } from '@web/core/l10n/translation';
import { rpc, RPCError } from '@web/core/network/rpc';
import { patch } from '@web/core/utils/patch';

import { PaymentForm } from '@payment/interactions/payment_form';

patch(PaymentForm.prototype, {

    /**
     * Override to set the payment flow to 'direct' for Djomy.
     * This allows us to capture the phone number from the inline form.
     *
     * @override
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'djomy') {
            await super._prepareInlineForm(...arguments);
            return;
        }

        if (flow === 'token') {
            return; // No inline form for tokens.
        }

        // Switch to direct flow to capture the phone number
        this._setPaymentFlow('direct');
    },

    /**
     * Process Djomy payment by capturing the phone and calling custom route.
     *
     * @override
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'djomy') {
            await super._processDirectFlow(...arguments);
            return;
        }

        // Get the phone number from the inline form
        const phoneInput = document.querySelector('#o_djomy_phone');
        const phone = phoneInput?.value?.trim();

        if (!phone) {
            this._displayErrorDialog(
                _t("Erreur"),
                _t("Le numero de telephone est requis pour le paiement Djomy")
            );
            this._enableButton();
            return;
        }

        try {
            // Call custom route with the phone number
            const result = await this.waitFor(rpc('/payment/djomy/process', {
                reference: processingValues.reference,
                phone: phone,
            }));

            if (result.error) {
                this._displayErrorDialog(_t("Erreur de paiement"), result.error);
                this._enableButton();
                return;
            }

            // Redirect to Djomy gateway
            window.location.href = result.redirect_url;

        } catch (error) {
            if (error instanceof RPCError) {
                this._displayErrorDialog(_t("Erreur de paiement"), error.data.message);
                this._enableButton();
            } else {
                return Promise.reject(error);
            }
        }
    },

});
