# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Extensión de product.template para modo vending.
"""

import logging
from odoo.tools import html2plaintext  # type: ignore
from odoo import models, fields, api, _  # type: ignore

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    _VENDING_CATALOG_TRIGGER_FIELDS = {
        'name',
        'public_description',
        'list_price',
        'image_1920',
        'active',
        'available_in_pos',
        'sale_ok',
    }
    
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

    @staticmethod
    def _to_public_description_text(description_html):
        """Convierte HTML de Odoo a texto plano apto para el kiosk."""
        if not description_html:
            return False

        text = html2plaintext(description_html or '').strip()
        return text or False

    def _notify_vending_catalog_changes(self, reason='product_template_update'):
        """Notifica al kiosco cuando cambia metadata relevante de productos."""
        machines = self.mapped('vending_slot_ids.machine_id')
        if not machines:
            return

        _logger.info(
            "[Vending Bus] Notificando cambio de catálogo (%s) para %s máquina(s) por %s producto(s)",
            reason,
            len(machines),
            len(self),
        )
        self.env['stock.quant']._notify_vending_changes_for_machines(machines)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if any(self._VENDING_CATALOG_TRIGGER_FIELDS.intersection(vals.keys()) for vals in vals_list):
            records._notify_vending_catalog_changes(reason='product_template_create')
        return records

    def write(self, vals):
        result = super().write(vals)
        if self._VENDING_CATALOG_TRIGGER_FIELDS.intersection(vals.keys()):
            self._notify_vending_catalog_changes(reason='product_template_write')
        return result

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

        # Modo vending: solo productos con slots activos y stock.
        available_product_ids = config.get_available_vending_product_ids()
        _logger.info("[Vending] Productos disponibles con stock: %s (IDs: %s)",
                     len(available_product_ids), available_product_ids)

        if not available_product_ids:
            # Sin productos disponibles: devolver lista vacía
            _logger.warning("[Vending] No hay productos con stock disponible")
            return []
        
        # Aplicar filtro vending directamente en la búsqueda
        domain = self._load_pos_self_data_domain(response, config)
        domain = domain + [
            ('id', 'in', available_product_ids),
            ('company_id', 'in', [config.company_id.id, False]),
        ]
        _logger.debug("[Vending] Domain final (company_id=%s): %s", config.company_id.id, domain)

        records = self.search(domain)
        ordering_index = {product_id: index for index, product_id in enumerate(available_product_ids)}
        records = records.sorted(
            key=lambda product: (
                ordering_index.get(product.id, 10**9),
                (product.display_name or product.name or '').lower(),
                product.id,
            )
        )

        _logger.info("[Vending] Productos cargados para self-order: %s", len(records))
        result = self._load_pos_self_data_read(records, config)

        # Agregar public_description a cada producto para el kiosk
        descriptions = {
            record.id: self._to_public_description_text(record.public_description)
            for record in records
        }
        for record_data in result:
            record_data['public_description'] = descriptions.get(record_data['id'], False)

        return result