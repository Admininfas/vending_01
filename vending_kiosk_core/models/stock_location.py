# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión de stock.location para visualizar relación con slots.
"""

import logging
from odoo import models, fields, api, _  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore

_logger = logging.getLogger(__name__)


class StockLocation(models.Model):
    _inherit = 'stock.location'

    vending_slot_id = fields.Many2one(
        'vending.slot',
        string='Slot de Máquina Expendedora',
        compute='_compute_vending_slot_id',
        help='Slot de máquina expendedora asociado a esta ubicación'
    )

    @api.depends()
    def _compute_vending_slot_id(self):
        """Compute el slot asociado a esta ubicación."""
        for record in self:
            slot = self.env['vending.slot'].search([('location_id', '=', record.id)], limit=1)
            record.vending_slot_id = slot.id if slot else False