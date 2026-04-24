# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Modelo para slots de vending machines.

Cada slot representa una posición física en la máquina
donde se almacenan productos para dispensar.
"""
 
from odoo import api, fields, models, _  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore


class VendingSlot(models.Model):
    """Slot de una máquina expendedora que contiene productos."""

    _name = 'vending.slot'
    _description = 'Slot de Máquina Expendedora'
    _rec_name = 'name'
    _order = 'machine_id, code'
    _inherit = ['mail.thread']

    machine_id = fields.Many2one(
        'vending.machine',
        string='Máquina',
        required=True,
        tracking=True,
        ondelete='cascade',
        index=True,
    )
    name = fields.Char(
        string='Etiqueta',
        required=True,
        tracking=True,
        help='Etiqueta del slot (ej: A1)'
    )
    code = fields.Integer(
        string='Número de slot',
        required=True,
        tracking=True,
        help='Número único que identifica este slot en el hardware de la máquina expendedora'
    )
    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Producto',
        required=True,
        tracking=True,
        help='Producto asignado a este slot'
    )
    # Ubicación de stock para este slot
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicación de Stock',
        required=True,
        tracking=True,
        help='Ubicación de stock específica para este slot'
    )
    
    # Estado del slot
    is_active = fields.Boolean(
        string='Activo',
        default=True,
        tracking=True,
        help='Si el slot está activo y puede dispensar'
    )
    is_fault_blocked = fields.Boolean(
        string='Desactivado por falla',
        default=False,
        tracking=True,
        help='Indica si el slot está desactivado por una alarma de falla.',
    )
    
    # Campos computados usando stock real
    current_stock = fields.Float(
        string='Stock Actual',
        compute='_compute_current_stock',
        store=True,
        help='Stock disponible en la ubicación específica del slot'
    )

    _VENDING_SLOT_CATALOG_FIELDS = {
        'machine_id',
        'name',
        'code',
        'product_tmpl_id',
        'location_id',
        'is_active',
        'is_fault_blocked',
    }

    def _notify_vending_slot_catalog_changes(self, machines=None, reason='slot_update'):
        """Dispara actualización de catálogo para las máquinas impactadas por slots."""
        target_machines = machines or self.mapped('machine_id')
        if not target_machines:
            return

        self.env['stock.quant']._notify_vending_changes_for_machines(target_machines)

    @api.depends('location_id', 'product_tmpl_id')
    def _compute_current_stock(self):
        """Computa el stock real disponible en la ubicación del slot."""
        for slot in self:
            if not slot.location_id or not slot.product_tmpl_id:
                slot.current_stock = 0.0
                continue
            
            # Buscar stock.quant específico usando available_quantity
            quants = self.env['stock.quant'].search([
                ('location_id', '=', slot.location_id.id),
                ('product_id.product_tmpl_id', '=', slot.product_tmpl_id.id),
            ])
            slot.current_stock = sum(quants.mapped('available_quantity'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._notify_vending_slot_catalog_changes(reason='slot_create')
        return records

    def write(self, vals):
        old_machines = self.mapped('machine_id')
        result = super().write(vals)
        if self._VENDING_SLOT_CATALOG_FIELDS.intersection(vals.keys()):
            affected_machines = (old_machines | self.mapped('machine_id'))
            self._notify_vending_slot_catalog_changes(
                machines=affected_machines,
                reason='slot_write',
            )
        return result

    def unlink(self):
        affected_machines = self.mapped('machine_id')
        result = super().unlink()
        if affected_machines:
            self._notify_vending_slot_catalog_changes(
                machines=affected_machines,
                reason='slot_unlink',
            )
        return result

    @api.constrains('machine_id', 'code')
    def _check_unique_code_per_machine(self):
        """
        Valida que el código del slot sea único por máquina.
        """
        for record in self:
            if not record.machine_id or record.code is None:
                continue
            existing = self.search_count([
                ('machine_id', '=', record.machine_id.id),
                ('code', '=', record.code),
                ('id', '!=', record.id),
            ])
            if existing:
                raise ValidationError(_(
                    'El código del slot debe ser único por máquina.'
                ))

    @api.constrains('location_id')
    def _check_unique_location(self):
        """
        Valida que cada ubicación esté asociada a un único slot.
        """
        for record in self:
            if not record.location_id:
                continue
            existing = self.search_count([
                ('location_id', '=', record.location_id.id),
                ('id', '!=', record.id),
            ])
            if existing:
                raise ValidationError(_(
                    'Cada ubicación solo puede estar asociada a un slot.'
                ))

    @api.constrains('product_tmpl_id', 'machine_id')
    def _check_product_company(self):
        """
        Valida que el producto asignado al slot pertenezca a la misma
        compañía de la máquina o sea compartido (company_id = False).
        """
        for record in self:
            if not record.product_tmpl_id or not record.machine_id:
                continue
            product_company = record.product_tmpl_id.company_id
            machine_company = record.machine_id.company_id
            if product_company and machine_company and product_company != machine_company:
                raise ValidationError(_(
                    'El producto "%(product)s" pertenece a la empresa "%(product_company)s" '
                    'y no puede asignarse a un slot de la máquina "%(machine)s" '
                    'que pertenece a "%(machine_company)s". '
                    'Solo se permiten productos de la misma empresa o compartidos (sin empresa asignada).',
                    product=record.product_tmpl_id.name,
                    product_company=product_company.name,
                    machine=record.machine_id.name,
                    machine_company=machine_company.name,
                ))