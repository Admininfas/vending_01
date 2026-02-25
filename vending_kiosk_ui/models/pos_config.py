# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Extensión de pos.config para modo Vending.

Implementa los métodos de apertura/cierre de sesión sin caja registradora,
replicando el comportamiento de kiosk pero para vending machines.

Clases:
    PosConfig: Extensión del modelo pos.config con soporte para vending.
"""

import logging
from odoo import fields, models, api, _  # type: ignore[import]

_logger = logging.getLogger(__name__)

# Constante para el monto inicial de caja en modo vending
VENDING_INITIAL_CASH = 0
VENDING_INITIAL_NOTES = ""


class PosConfig(models.Model):
    """Extensión de POS Config para soporte de máquinas vending.
    
    Agrega el modo 'vending' al selector de self_ordering_mode y proporciona
    métodos para abrir/cerrar sesiones sin requerir apertura manual de caja.
    
    Attributes:
        self_ordering_mode: Selector extendido con opción 'vending'.
    """
    
    _inherit = 'pos.config'

    self_ordering_mode = fields.Selection(
        selection_add=[('vending', 'Vending Machine')], 
        ondelete={'vending': 'cascade'},
    )

    def action_open_vending(self):
        """Abre la sesión de vending sin requerir apertura manual de caja.
        
        Replica el comportamiento de action_open_wizard() de pos_self_order
        pero para el modo vending. La sesión se crea automáticamente con
        set_opening_control(0, "") lo cual inicializa la caja con $0.
        
        Returns:
            dict: Acción para abrir URL de self ordering en nueva ventana.
        """
        self.ensure_one()
        
        if not self.current_session_id:
            # Verificar si se puede crear una nueva sesión
            res = self._check_before_creating_new_session()
            if res:
                return res
            
            # Crear sesión automáticamente
            session = self.env['pos.session'].create({
                'user_id': self.env.uid,
                'config_id': self.id
            })
            # Inicializar caja con $0 (sin apertura manual)
            session.set_opening_control(VENDING_INITIAL_CASH, VENDING_INITIAL_NOTES)
            
            # Notificar cambio de estado si el método existe
            if hasattr(self, '_notify'):
                self._notify('STATUS', {'status': 'open'})
            
            _logger.info("[Vending] Sesión creada para POS %s (ID: %s)", self.name, self.id)
        
        return {
            'type': 'ir.actions.act_url',
            'name': _('Vending Machine'),
            'target': 'new',
            'url': self.self_ordering_url,
        }

    def action_close_vending_session(self):
        """Cierra la sesión de vending.
        
        Elimina órdenes en borrador y cierra la sesión, replicando
        el comportamiento de action_close_kiosk_session().
        
        Returns:
            dict | bool: Acción de cierre de sesión o True si no hay sesión.
        """
        self.ensure_one()
        
        if not self.current_session_id:
            return True
            
        # Eliminar órdenes en borrador
        if self.current_session_id.order_ids:
            draft_orders = self.current_session_id.order_ids.filtered(
                lambda o: o.state == 'draft'
            )
            if draft_orders:
                _logger.info(
                    "[Vending] Eliminando %d órdenes en borrador para POS %s",
                    len(draft_orders),
                    self.name
                )
                draft_orders.unlink()
        
        # Notificar cambio de estado si el método existe
        if hasattr(self, '_notify'):
            self._notify('STATUS', {'status': 'closed'})
        
        _logger.info("[Vending] Cerrando sesión para POS %s", self.name)
        
        return self.current_session_id.action_pos_session_closing_control()

    def close_ui(self):
        """Sobrescribe close_ui para manejar el cierre de UI en modo vending.
        
        Similar a como pos_self_order lo hace para kiosk.
        
        Returns:
            dict | bool: Resultado del cierre de sesión.
        """
        if self.self_ordering_mode == "vending":
            return self.action_close_vending_session()
        return super().close_ui()
