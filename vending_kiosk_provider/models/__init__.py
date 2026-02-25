# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from . import vending_webhook_log  # Cargar primero el modelo base
from . import vending_provider_client
from . import pos_config
from . import pos_order  # Cargar después la extensión que lo referencia