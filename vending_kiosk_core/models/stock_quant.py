# -*- coding: utf-8 -*-

from odoo import models, api, fields, _  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore
import logging

_logger = logging.getLogger(__name__)


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.constrains('location_id', 'product_id', 'quantity')
    def _check_slot_product_consistency(self):
        """
        Valida que una ubicación asociada a un slot solo pueda tener stock
        del producto asignado a ese slot.
        """
        for quant in self:
            # Solo aplicar a quants con cantidad positiva en ubicaciones válidas
            if not quant.location_id or not quant.product_id or quant.quantity <= 0:
                continue
            
            # Buscar si esta ubicación está asociada a un slot
            slot = self.env['vending.slot'].search([
                ('location_id', '=', quant.location_id.id)
            ], limit=1)
            
            if not slot:
                # No hay slot, no validar
                continue
            
            # Verificar que el producto del quant coincida con el del slot
            if quant.product_id.product_tmpl_id != slot.product_tmpl_id:
                raise ValidationError(_(
                    'La ubicación "%(location)s" está asignada al slot "%(slot)s" '
                    'que solo puede contener el producto "%(slot_product)s". '
                    'No se puede registrar stock del producto "%(quant_product)s" en esta ubicación.',
                    location=quant.location_id.display_name,
                    slot=slot.name,
                    slot_product=slot.product_tmpl_id.name,
                    quant_product=quant.product_id.display_name
                ))

    @api.model_create_multi
    def create(self, vals_list):
        """
        Al crear stock.quant, recalcular current_stock de slots afectados
        y notificar cambios vía bus.
        """
        quants = super().create(vals_list)
        self._update_vending_slots(quants)
        self._notify_vending_product_changes(quants)
        return quants

    def write(self, vals):
        """
        Al modificar stock.quant, recalcular current_stock de slots afectados
        y notificar cambios vía bus.
        """
        # Guardar estados antes del cambio
        old_data = [(q.location_id.id, q.product_id.product_tmpl_id.id) for q in self]
        
        result = super().write(vals)
        
        # Recalcular para ubicaciones/productos afectados
        self._update_vending_slots(self)
        
        # Si cambió location_id o product_id, recalcular también los antiguos
        if 'location_id' in vals or 'product_id' in vals:
            for old_location_id, old_product_tmpl_id in old_data:
                old_slots = self.env['vending.slot'].search([
                    ('location_id', '=', old_location_id),
                    ('product_tmpl_id', '=', old_product_tmpl_id),
                ])
                if old_slots:
                    old_slots._compute_current_stock()
        
        # Notificar cambios vía bus
        self._notify_vending_product_changes(self)
        
        return result

    def unlink(self):
        """
        Al eliminar stock.quant, recalcular current_stock de slots afectados
        y notificar cambios vía bus.
        """
        # Guardar info antes de eliminar (necesitamos notificar ANTES de eliminar)
        affected_machines = self._get_affected_vending_machines()
        affected_slots_data = [(q.location_id.id, q.product_id.product_tmpl_id.id) for q in self]
        
        result = super().unlink()
        
        # Recalcular slots que tenían estos quants
        for location_id, product_tmpl_id in affected_slots_data:
            slots = self.env['vending.slot'].search([
                ('location_id', '=', location_id),
                ('product_tmpl_id', '=', product_tmpl_id),
            ])
            if slots:
                slots._compute_current_stock()
        
        # Notificar cambios (usando las máquinas guardadas antes del unlink)
        self._notify_vending_changes_for_machines(affected_machines)
        
        return result

    def _update_vending_slots(self, quants):
        """
        Recalcular current_stock para slots afectados por estos stock.quant.
        """
        if not quants:
            return
            
        # Obtener combinaciones únicas de (location_id, product_tmpl_id)
        affected_combinations = set()
        for quant in quants:
            if quant.location_id and quant.product_id:
                affected_combinations.add((
                    quant.location_id.id, 
                    quant.product_id.product_tmpl_id.id
                ))
        
        # Buscar y recalcular slots afectados
        for location_id, product_tmpl_id in affected_combinations:
            slots = self.env['vending.slot'].search([
                ('location_id', '=', location_id),
                ('product_tmpl_id', '=', product_tmpl_id),
            ])
            if slots:
                slots._compute_current_stock()
                _logger.debug(f"Recalculado current_stock para {len(slots)} slots de producto {product_tmpl_id} en ubicación {location_id}")
    
    def _get_affected_vending_machines(self):
        """
        Retorna las vending.machine afectadas por cambios en estos stock.quant.
        """
        if not self:
            return self.env['vending.machine'].browse()
        
        # Buscar slots que usan estas ubicaciones
        location_ids = self.mapped('location_id').ids
        if not location_ids:
            return self.env['vending.machine'].browse()
        
        slots = self.env['vending.slot'].search([
            ('location_id', 'in', location_ids)
        ])
        
        return slots.mapped('machine_id')
    
    def _notify_vending_product_changes(self, quants):
        """
        Notifica vía bus.bus cuando cambian productos disponibles en vending machines.
        Envía notificación a cada pos.config que usa las máquinas afectadas.
        """
        if not quants:
            return
        
        # Obtener máquinas afectadas
        affected_machines = quants._get_affected_vending_machines()
        
        if not affected_machines:
            _logger.debug("[Vending Bus] No hay máquinas afectadas por cambio de stock")
            return
        
        self._notify_vending_changes_for_machines(affected_machines)
    
    def _notify_vending_changes_for_machines(self, machines):
        """
        Envía notificaciones bus para las máquinas especificadas.
        OPTIMIZADO: Filtra por compañía para evitar notificar a empresas irrelevantes.
        """
        if not machines:
            return
        
        # Obtener las compañías de las máquinas afectadas
        affected_company_ids = machines.mapped('company_id').ids
        
        # Buscar pos.config que usan estas máquinas Y pertenecen a las compañías afectadas
        pos_configs = self.env['pos.config'].search([
            ('vending_machine_id', 'in', machines.ids),
            ('self_ordering_mode', '=', 'vending'),
            ('company_id', 'in', affected_company_ids)
        ])
        
        if not pos_configs:
            _logger.debug(f"[Vending Bus] No hay POS configs asociados a máquinas {machines.mapped('name')}")
            return
        
        # Enviar notificación a cada pos.config
        for config in pos_configs:
            try:
                channel = f'vending_products_{config.id}'
                
                # Calcular productos actuales con stock
                current_products = config.get_available_vending_product_ids()
                
                # Enviar notificación con lista completa
                # El frontend calculará el delta comparando con su estado local
                message = {
                    'type': 'vending_products_update',
                    'channel': channel,
                    'pos_config_id': config.id,
                    'machine_id': config.vending_machine_id.id,
                    'machine_name': config.vending_machine_id.name,
                    'timestamp': fields.Datetime.now().isoformat(),
                    'all_available_ids': current_products,
                }
                
                self.env['bus.bus']._sendone(channel, 'notification', message)
                
                _logger.info(
                    f"[Vending Bus] Notificación enviada al canal {channel} "
                    f"(máquina: {config.vending_machine_id.name}): "
                    f"{len(current_products)} productos disponibles"
                )
                
            except Exception as e:
                _logger.error(
                    f"[Vending Bus] Error enviando notificación para pos.config {config.id}: {e}",
                    exc_info=True
                )