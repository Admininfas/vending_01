# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión del modelo account.move para agregar referencia a vending.
"""

from odoo import models, fields, api # type: ignore


class AccountMove(models.Model):
    _inherit = 'account.move'

    vending_order_id = fields.Many2one(
        'pos.order',
        string='Orden de Vending',
        index=True,
        help='Orden de máquina expendedora que generó esta factura'
    )
    is_vending_invoice = fields.Boolean(
        string='Factura de Vending',
        compute='_compute_is_vending_invoice',
        store=True,
        help='Indica si esta factura fue generada por una máquina expendedora'
    )

    @api.depends('vending_order_id')
    def _compute_is_vending_invoice(self):
        """Calcula si la factura es de vending basado en la orden."""
        for record in self:
            record.is_vending_invoice = bool(record.vending_order_id)