# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión de pos.config para indicar si el proveedor de vending está en modo dummy.
"""

from odoo import api, fields, models  # type: ignore


class PosConfig(models.Model):
    """Agrega campo computado para detectar modo dummy del proveedor."""

    _inherit = 'pos.config'

    vending_is_dummy_mode = fields.Boolean(
        string='Modo Dummy activo',
        compute='_compute_vending_is_dummy_mode',
        help='Indica si el proveedor de pagos está usando el endpoint dummy local '
             'en lugar de Winfas. Configure vending.provider_base_url en '
             'Parámetros del Sistema para desactivar.',
    )

    @api.depends('vending_machine_id')
    def _compute_vending_is_dummy_mode(self):
        """Calcula si el proveedor opera en modo dummy (sin URL externa)."""
        provider_url = self.env['ir.config_parameter'].sudo().get_param(
            'vending.provider_base_url', default='',
        )
        is_dummy = not bool(provider_url)
        for record in self:
            record.vending_is_dummy_mode = is_dummy
