# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
from pathlib import Path

from . import controllers
from . import models

from odoo.addons.payment import setup_provider, reset_payment_provider


def post_init_hook(env):
    setup_provider(env, 'djomy')
    _load_payment_method_image(env)


def _load_payment_method_image(env):
    """Load the Djomy payment method image.

    This ensures the image is set even for existing installations
    where the payment method was created before the image was added.
    """
    module_path = Path(__file__).parent
    image_path = module_path / 'static' / 'src' / 'img' / 'djomy.png'

    if image_path.exists():
        image_data = base64.b64encode(image_path.read_bytes())
        payment_method = env.ref('payment_djomy.payment_method_djomy', raise_if_not_found=False)
        if payment_method:
            payment_method.write({'image': image_data})


def uninstall_hook(env):
    reset_payment_provider(env, 'djomy')
