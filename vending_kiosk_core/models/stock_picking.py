# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión del modelo stock.picking para agregar referencia a vending.
"""

from odoo import models, fields, api # type: ignore


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    vending_order_id = fields.Many2one(
        'pos.order',
        string='Orden de Vending',
        index=True,
        help='Orden de máquina expendedora que generó esta entrega'
    )
    is_vending_delivery = fields.Boolean(
        string='Entrega de Vending',
        compute='_compute_is_vending_delivery',
        store=True,
        help='Indica si esta entrega fue generada por una máquina expendedora'
    )
    vending_machine_id = fields.Many2one(
        'vending.machine',
        string='Máquina Expendedora',
        related='vending_order_id.vending_machine_id',
        store=True,
        help='Máquina expendedora que generó esta entrega'
    )

    @api.depends('vending_order_id')
    def _compute_is_vending_delivery(self):
        """Calcula si la entrega es de vending basado en la orden."""
        for record in self:
            record.is_vending_delivery = bool(record.vending_order_id)