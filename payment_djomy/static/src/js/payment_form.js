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
     * Cree le lien de paiement Djomy puis y envoie le client.
     *
     * Le numero de telephone est optionnel : s'il est renseigne, Djomy
     * envoie aussi le lien par SMS, ce qui permet au client de payer plus
     * tard depuis son telephone si la page est fermee.
     *
     * @override
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'djomy') {
            await super._processDirectFlow(...arguments);
            return;
        }

        const phoneInput = document.querySelector('#o_djomy_phone');
        const phone = phoneInput?.value?.trim() || null;

        try {
            const result = await this.waitFor(rpc('/payment/djomy/process', {
                reference: processingValues.reference,
                phone: phone,
            }));

            if (result.error) {
                this._displayErrorDialog(_t("Erreur de paiement"), result.error);
                this._enableButton();
                return;
            }

            // Page de paiement Djomy (lien de paiement)
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
