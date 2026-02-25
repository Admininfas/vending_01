# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión de stock.warehouse para visualizar máquinas vending.
"""

import logging
from odoo import models, fields, api, _  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore

_logger = logging.getLogger(__name__)


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    vending_machine_id = fields.Many2one(
        'vending.machine',
        string='Máquina Expendedora',
        compute='_compute_vending_machine_id',
        help='Máquina expendedora asociada a este almacén'
    )

    @api.depends()
    def _compute_vending_machine_id(self):
        """Compute la máquina asociada a este almacén."""
        for record in self:
            machine = self.env['vending.machine'].search([('warehouse_id', '=', record.id)], limit=1)
            record.vending_machine_id = machine.id if machine else False
