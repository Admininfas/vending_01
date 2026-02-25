# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión de pos.config para vending machines.
"""

import logging
import pytz # type: ignore
from collections import defaultdict
from odoo import models, fields, api, _  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore

_logger = logging.getLogger(__name__)


class PosConfig(models.Model):
    _inherit = 'pos.config'

    vending_machine_id = fields.Many2one(
        'vending.machine',
        string='Máquina Expendedora',
        help='Máquina expendedora asociada a este punto de venta'
    )
    vending_countdown_seconds = fields.Integer(
        string='Tiempo de espera vending (segundos)',
        related='vending_machine_id.countdown_seconds',
        readonly=False,
        help='Tiempo en segundos antes de volver automáticamente al menú principal tras una operación'
    )
    vending_qr_timeout_seconds = fields.Integer(
        string='Timeout de QR vending (segundos)',
        related='vending_machine_id.qr_timeout_seconds',
        readonly=False,
        help='Tiempo en segundos de vida del QR de pago antes de expirar'
    )
    vending_invoice_journal_id = fields.Many2one(
        'account.journal',
        string='Diario de facturas vending',
        related='vending_machine_id.invoice_journal_id',
        readonly=True,
        help='Diario donde se crearán las facturas de vending'
    )

    def write(self, vals):
        """Sincronizar relación bidireccional POS-Vending al escribir."""
        # Solo sincronizar si no estamos en un contexto de sincronización
        if 'vending_machine_id' in vals and not self.env.context.get('skip_vending_sync'):
            # Limpiar referencias anteriores ANTES del write
            for record in self:
                if record.vending_machine_id:
                    record.vending_machine_id.with_context(skip_pos_sync=True).write({'pos_config_id': False})
        
        result = super().write(vals)
        
        # Establecer nueva referencia DESPUÉS del write
        if 'vending_machine_id' in vals and not self.env.context.get('skip_vending_sync'):
            for record in self:
                if record.vending_machine_id:
                    record.vending_machine_id.with_context(skip_pos_sync=True).write({
                        'pos_config_id': record.id
                    })
        
        return result

    def get_available_vending_products(self):
        """
        Retorna productos disponibles para esta máquina expendedora con stock real > 0.
        Solo se usa cuando self_ordering_mode == 'vending'.
        """
        self.ensure_one()
        
        if not self.vending_machine_id:
            _logger.warning("[Vending] get_available_vending_products: No hay máquina configurada")
            return self.env['product.template'].browse()
        
        # Forzar recálculo de current_stock antes de buscar
        all_slots = self.env['vending.slot'].search([
            ('machine_id', '=', self.vending_machine_id.id),
            ('is_active', '=', True),
            ('location_id', '!=', False),
        ])
        
        # Forzar recálculo del campo computado
        all_slots._compute_current_stock()
        
        _logger.info("[Vending] Slots de la máquina %s: %s", 
                     self.vending_machine_id.name, 
                     [(s.name, s.product_tmpl_id.name, s.current_stock) for s in all_slots])
        
        # Ahora buscar slots con stock > 0
        slots_with_stock = all_slots.filtered(lambda s: s.current_stock > 0)
        
        _logger.info("[Vending] Slots con stock > 0: %s", 
                     [(s.name, s.product_tmpl_id.name, s.current_stock) for s in slots_with_stock])
        
        products = slots_with_stock.mapped('product_tmpl_id')
        
        # Filtrar productos por company_id: solo los de esta compañía o compartidos (sin compañía)
        company_id = self.company_id.id
        products = products.filtered(
            lambda p: not p.company_id or p.company_id.id == company_id
        )
        _logger.info("[Vending] Productos disponibles (filtrados por company_id=%s): %s (IDs: %s)", 
                     company_id, products.mapped('name'), products.ids)
        
        return products
    
    def get_available_vending_product_ids(self):
        """
        Retorna solo los IDs de productos disponibles con stock > 0.
        Versión optimizada para llamadas desde frontend vía RPC.
        """
        products = self.get_available_vending_products()
        return products.ids
    
    def get_best_slot_for_product(self, product_tmpl_id):
        """
        Retorna el slot con mayor stock disponible para el producto.
        """
        self.ensure_one()
        
        if not self.vending_machine_id:
            return self.env['vending.slot'].browse()
        
        slots = self.env['vending.slot'].search([
            ('machine_id', '=', self.vending_machine_id.id),
            ('product_tmpl_id', '=', product_tmpl_id),
            ('is_active', '=', True),
            ('location_id', '!=', False),
            ('current_stock', '>', 0),
        ], order='current_stock desc', limit=1)
        
        return slots

    def get_slots_for_product(self, product_tmpl_id):
        """
        Retorna todos los slots disponibles con stock para un producto.
        Usado para mostrar en el catálogo de productos.
        """
        self.ensure_one()
        
        if not self.vending_machine_id:
            return []
        
        slots = self.env['vending.slot'].search([
            ('machine_id', '=', self.vending_machine_id.id),
            ('product_tmpl_id', '=', product_tmpl_id),
            ('is_active', '=', True),
            ('location_id', '!=', False),
            ('current_stock', '>', 0),
        ], order='code')
        
        return [{
            'code': slot.code,
            'name': slot.name,
            'stock': slot.current_stock,
        } for slot in slots]

    def get_all_product_slots(self):
        """
        Retorna un diccionario con los slots disponibles para cada producto.
        Optimizado para cargar todo de una vez en el frontend.
        
        Returns:
            dict: {product_id: [{'code': int, 'name': str, 'stock': float}, ...]}
        """
        self.ensure_one()
        
        if not self.vending_machine_id:
            return {}
        
        # Buscar todos los slots activos con stock
        slots = self.env['vending.slot'].search([
            ('machine_id', '=', self.vending_machine_id.id),
            ('is_active', '=', True),
            ('location_id', '!=', False),
            ('current_stock', '>', 0),
        ], order='code')
        
        # Agrupar por producto
        result = {}
        for slot in slots:
            product_id = slot.product_tmpl_id.id
            if product_id not in result:
                result[product_id] = []
            result[product_id].append({
                'code': slot.code,
                'name': slot.name,
                'stock': slot.current_stock,
            })
        
        return result

    @api.model
    def _load_pos_self_data_search_read(self, response, config):
        """
        Extender la carga de datos para filtrar productos en modo vending.
        """
        records = super()._load_pos_self_data_search_read(response, config)
        
        _logger.info("[Vending] _load_pos_self_data_search_read (pos.config) - modo: %s", 
                     config.self_ordering_mode)
        
        # Si está en modo vending, agregar información específica
        if config.self_ordering_mode == 'vending':
            if not config.vending_machine_id:
                # Si no hay máquina configurada, marcar para mostrar mensaje
                _logger.warning("[Vending] No hay máquina configurada para POS %s", config.id)
                records[0]['_vending_no_machine'] = True
                records[0]['_vending_available_products'] = []
                records[0]['_vending_product_slots'] = {}
                records[0]['vending_countdown_seconds'] = 40  # Valor por defecto
                records[0]['vending_qr_timeout_seconds'] = 120  # Valor por defecto
            else:
                # Obtener productos disponibles para esta máquina
                available_products = config.get_available_vending_products()
                product_slots = config.get_all_product_slots()
                records[0]['_vending_available_products'] = available_products.ids
                records[0]['_vending_product_slots'] = product_slots
                records[0]['_vending_machine_id'] = config.vending_machine_id.id
                records[0]['vending_countdown_seconds'] = config.vending_countdown_seconds or 40
                records[0]['vending_qr_timeout_seconds'] = config.vending_qr_timeout_seconds or 120
                _logger.info("[Vending] Enviando al frontend _vending_available_products: %s", 
                             available_products.ids)
                _logger.info("[Vending] Enviando al frontend _vending_product_slots: %s productos con slots", 
                             len(product_slots))
                
        return records
    
    def get_statistics_for_session(self, session):
        """
        Parche para el método original de odoo. Ahora no se consideran 
        órdenes que están en estado draft pero que tienen un estado de vending que indica que no se completarán (qr_expired, user_cancelled, payment_error, vending_delivery_error).
        Esto es porque en vending el proceso de pago es externo y puede haber órdenes que queden en draft pero que no se completarán nunca, lo que distorsionaría las estadísticas si se contaran como órdenes activas.
        """
        self.ensure_one()
        currency = self.currency_id
        timezone = pytz.timezone(self.env.context.get('tz') or self.env.user.tz or 'UTC')
        statistics = {
            'cash': {
                'raw_opening_cash': session.cash_register_balance_start,
                'opening_cash': currency.format(session.cash_register_balance_start)
            },
            'date': {
                'is_started': bool(session.start_at),
                'start_date': session.start_at.astimezone(timezone).strftime('%b %d') if session.start_at else False,
            },
            'orders': {
                'paid': False,
                'draft': False,
            },
        }

        all_paid_orders = session.order_ids.filtered(lambda o: o.state in ['paid', 'done'])
        refund_orders = all_paid_orders.filtered(lambda o: o.is_refund)
        draft_orders = session.order_ids.filtered(lambda o: (o.state == 'draft' and not o.vending_status in ['qr_expired', 'user_cancelled', 'payment_error', 'vending_delivery_error']))
        non_refund_orders = all_paid_orders - refund_orders

        # calculate total refunded amount per original order for refund count check
        refund_totals = defaultdict(float)
        for refund in refund_orders:
            if refund.refunded_order_id:
                refund_totals[refund.refunded_order_id.id] += abs(refund.amount_total)

        # count paid orders that are not completely refunded
        paid_order_count = sum(
            1 for order in non_refund_orders
            if refund_totals.get(order.id, 0.0) != order.amount_total
        )

        if paid_order_count:
            total_paid = sum(all_paid_orders.mapped('amount_total'))
            statistics['orders']['paid'] = {
                'amount': total_paid,
                'count': paid_order_count,
                'display': f"{currency.format(total_paid)} ({paid_order_count} {'order' if paid_order_count == 1 else 'orders'})"
            }

        if draft_orders:
            total_draft = sum(draft_orders.mapped('amount_total'))
            count_draft = len(draft_orders)
            statistics['orders']['draft'] = {
                'amount': total_draft,
                'count': count_draft,
                'display': f"{currency.format(total_draft)} ({count_draft} {'order' if count_draft == 1 else 'orders'})"
            }

        return statistics
