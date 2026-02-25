# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión de product.template para modo vending.
"""

import logging
from odoo import models, fields, api, _  # type: ignore

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'
    
    # Relación inversa para acceder a slots
    vending_slot_ids = fields.One2many(
        'vending.slot',
        'product_tmpl_id',
        string='Slots de Expendedora'
    )

    # Descripción visible para el cliente en el kiosk
    public_description = fields.Html(
        string='Descripción Pública',
        sanitize_attributes=False,
        help='Descripción visible para el cliente en el kiosk vending'
    )

    @api.model
    def _load_pos_self_data_search_read(self, response, config):
        """
        Filtrar productos según modo vending.
        Solo muestra productos que tienen stock en los slots de la máquina.
        """
        _logger.info("[Vending] _load_pos_self_data_search_read llamado para config %s, modo: %s", 
                     config.id, config.self_ordering_mode)
        
        if config.self_ordering_mode != 'vending':
            # Modo normal: usar lógica estándar
            return super()._load_pos_self_data_search_read(response, config)
        
        if not config.vending_machine_id:
            # Sin máquina configurada: no mostrar productos
            _logger.warning("[Vending] No hay máquina expendedora configurada para POS %s", config.id)
            return []
        
        # Modo vending: solo productos con slots activos y stock
        available_products = config.get_available_vending_products()
        _logger.info("[Vending] Productos disponibles con stock: %s (IDs: %s)", 
                     len(available_products), available_products.ids)
        
        if not available_products:
            # Sin productos disponibles: devolver lista vacía
            _logger.warning("[Vending] No hay productos con stock disponible")
            return []
        
        # Aplicar filtro vending directamente en la búsqueda
        domain = self._load_pos_self_data_domain(response, config)
        domain = domain + [
            ('id', 'in', available_products.ids),
            ('company_id', 'in', [config.company_id.id, False]),
        ]
        _logger.debug("[Vending] Domain final (company_id=%s): %s", config.company_id.id, domain)
        
        records = self.search(domain)
        _logger.info("[Vending] Productos cargados para self-order: %s", len(records))
        result = self._load_pos_self_data_read(records, config)

        # Agregar public_description a cada producto para el kiosk
        descriptions = {r.id: r.public_description or False for r in records}
        for record_data in result:
            record_data['public_description'] = descriptions.get(record_data['id'], False)

        return result