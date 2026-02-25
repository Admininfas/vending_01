# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión de pos.order para agregar relación con webhook logs.
"""

from odoo import api, fields, models, _  # type: ignore
from odoo.exceptions import UserError  # type: ignore


class PosOrder(models.Model):
    """Extensión de pos.order para logging de webhooks."""

    _inherit = 'pos.order'

    def action_open_webhook_logs(self):
        """Abre el listado de logs de webhook asociados a esta orden por referencia."""
        if not self.vending_reference:
            raise UserError(
                _('Esta orden no tiene una referencia de vending asignada')
            )
        
        # Buscar logs por campo indexado 'reference' (eficiente)
        logs = self.env['vending.webhook.log'].search([
            ('reference', '=', self.vending_reference)
        ])
        
        if not logs:
            # Fallback: búsqueda en payload para logs anteriores al campo reference
            logs = self.env['vending.webhook.log'].search([
                ('payload_json', 'ilike', self.vending_reference)
            ])
        
        if not logs:
            raise UserError(
                _('No hay registros de webhook asociados a esta orden')
            )
        
        # Abrir vista de árbol con los logs encontrados
        return {
            'type': 'ir.actions.act_window',
            'name': _('Logs de Webhook'),
            'res_model': 'vending.webhook.log',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', logs.ids)],
            'target': 'current',
        }