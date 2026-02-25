# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión del modelo pos.payment para agregar referencia a vending.
"""

from odoo import models, fields, api # type: ignore


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    vending_order_id = fields.Many2one(
        'pos.order',
        string='Orden de Vending',
        related='pos_order_id',
        store=True,
        help='Orden de máquina expendedora que generó este pago'
    )
    is_vending_payment = fields.Boolean(
        string='Pago de Vending',
        compute='_compute_is_vending_payment',
        store=True,
        help='Indica si este pago fue generado por una máquina expendedora'
    )

    @api.depends('pos_order_id.vending_machine_id')
    def _compute_is_vending_payment(self):
        """Calcula si el pago es de vending basado en la orden."""
        for record in self:
            record.is_vending_payment = bool(
                record.pos_order_id and 
                record.pos_order_id.vending_machine_id
            )