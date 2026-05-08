# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Modelo principal de máquinas vending.

Mantiene la identidad de hardware y la configuración de stock
asociada al almacén de la máquina.
"""

import base64
import hashlib
import hmac
import logging
from cryptography.fernet import Fernet, InvalidToken
from odoo import models, fields, api, _  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore

_logger = logging.getLogger(__name__)


class VendingMachine(models.Model):
    """Modelo de máquina expendedora."""

    _name = 'vending.machine'
    _description = 'Máquina Expendedora'
    _rec_name = 'name'
    _inherit = ['mail.thread']

    name = fields.Char(
        string='Nombre',
        required=True,
        tracking=True,
        help='Nombre descriptivo de la máquina'
    )
    code = fields.Char(
        string='Identificador de máquina',
        required=True,
        index=True,
        size=50,
        tracking=True,
        help='Identificador único de hardware que distingue esta máquina de las demás'
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string='Punto de Venta',
        required=True,
        tracking=True,
        help='Punto de venta asociado a la máquina'
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén',
        required=True,
        help='Almacén vinculado para gestión de stock'
    )
    slot_ids = fields.One2many(
        'vending.slot',
        'machine_id',
        string='Slots'
    )
    is_fault_blocked = fields.Boolean(
        string='Desactivada por falla',
        default=False,
        tracking=True,
        help='Indica si la máquina está desactivada por una alarma de falla.',
    )
    has_fault_blocked_slots = fields.Boolean(
        string='Tiene slots desactivados por falla',
        compute='_compute_fault_blocked_slot_stats',
        store=True,
    )
    fault_blocked_slots_count = fields.Integer(
        string='Slots desactivados por falla',
        compute='_compute_fault_blocked_slot_stats',
        store=True,
    )
    kiosk_refresh_token = fields.Integer(
        string='Token de refresh de kiosko',
        default=0,
        copy=False,
        help='Contador que se incrementa cuando un operador pide refrescar el kiosko. '
             'El frontend lo lee del polling y al detectar un cambio fuerza un reload.'
    )

    def action_refresh_kiosk_screen(self):
        """Incrementa kiosk_refresh_token. El polling del kiosko detecta el
        cambio (vía hash) y dispara `window.location.assign` a la raíz.
        """
        for machine in self:
            machine.kiosk_refresh_token = (machine.kiosk_refresh_token or 0) + 1
            _logger.info(
                "[Vending] Refresh manual de kiosko solicitado para %s (token=%s) por %s",
                machine.name, machine.kiosk_refresh_token, self.env.user.display_name,
            )
            machine.message_post(
                body=_(
                    "Refresh manual del kiosko solicitado (token #%(token)s).",
                    token=machine.kiosk_refresh_token,
                ),
            )
        return True
    countdown_seconds = fields.Integer(
        string='Tiempo de espera (segundos)',
        default=40,
        tracking=True,
        help='Tiempo en segundos antes de volver automáticamente al menú principal tras una operación'
    )
    qr_timeout_seconds = fields.Integer(
        string='Timeout de QR (segundos)',
        default=120,
        tracking=True,
        help='Tiempo en segundos de vida del QR de pago antes de expirar'
    )
    invoice_journal_id = fields.Many2one(
        'account.journal',
        string='Diario de Facturas',
        domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]",
        default=lambda self: self._default_invoice_journal(),
        tracking=True,
        help='Diario contable donde se crearán las facturas de las ventas de vending'
    )
    payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='Método de Pago Vending',
        tracking=True,
        default=lambda self: self._default_payment_method(),
        help='Método de pago que se usará para registrar las transacciones exitosas de vending'
    )
    anonymous_partner_id = fields.Many2one(
        'res.partner',
        string='Cliente Consumidor Anónimo',
        default=lambda self: self._default_anonymous_partner(),
        tracking=True,
        help='Cliente que se asignará automáticamente a las órdenes de vending para consumidores anónimos'
    )
    company_id = fields.Many2one(
        'res.company',
        related='pos_config_id.company_id',
        string='Compañía',
        readonly=True,
        store=True
    )
    api_key_input = fields.Char(
        string='API Key',
        copy=False,
        help='Campo de carga manual. Al guardar se cifra y no queda visible en texto plano.',
    )
    api_key_encrypted = fields.Text(
        string='API Key (cifrada)',
        copy=False,
        readonly=True,
        groups='vending_kiosk_core.group_vending_superadmin',
        help='Valor cifrado persistido en base de datos.',
    )
    api_key_last4 = fields.Char(
        string='API Key (ultimos 4)',
        size=4,
        readonly=True,
        groups='vending_kiosk_core.group_vending_superadmin',
    )
    api_key_set_at = fields.Datetime(
        string='API Key cargada en',
        readonly=True,
        groups='vending_kiosk_core.group_vending_superadmin',
    )
    api_key_set_by = fields.Many2one(
        'res.users',
        string='API Key cargada por',
        readonly=True,
        groups='vending_kiosk_core.group_vending_superadmin',
    )
    api_key_configured = fields.Boolean(
        string='API Key configurada',
        compute='_compute_api_key_configured',
        store=True,
    )

    @api.depends('api_key_encrypted')
    def _compute_api_key_configured(self):
        for record in self:
            record.api_key_configured = bool(record.api_key_encrypted)

    @api.depends('slot_ids.is_fault_blocked')
    def _compute_fault_blocked_slot_stats(self):
        for record in self:
            blocked_count = len(record.slot_ids.filtered('is_fault_blocked'))
            record.fault_blocked_slots_count = blocked_count
            record.has_fault_blocked_slots = bool(blocked_count)

    def _get_api_key_master_secret(self):
        """Obtiene la llave maestra desde parámetros del sistema."""
        secret = self.env['ir.config_parameter'].sudo().get_param(
            'vending.api_key_master_secret',
            default=''
        )
        if not secret:
            raise ValidationError(_(
                'No existe la llave maestra de API keys. Configure '
                'el parámetro del sistema "vending.api_key_master_secret".'
            ))
        return secret

    def _get_fernet(self):
        """Construye el cifrador Fernet a partir de la llave maestra."""
        secret = self._get_api_key_master_secret()
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode('utf-8')).digest())
        return Fernet(key)

    def _encrypt_api_key(self, plain_api_key):
        """Cifra una API key en texto plano."""
        if not plain_api_key:
            return False
        token = self._get_fernet().encrypt(plain_api_key.encode('utf-8'))
        return token.decode('utf-8')

    def _decrypt_api_key(self):
        """Descifra la API key almacenada para esta máquina."""
        self.ensure_one()
        if not self.api_key_encrypted:
            return ''
        try:
            token = self.api_key_encrypted.encode('utf-8')
            plain = self._get_fernet().decrypt(token)
            return plain.decode('utf-8')
        except (InvalidToken, ValueError, TypeError) as error:
            _logger.exception('No se pudo descifrar API key de máquina %s: %s', self.code, error)
            raise ValidationError(_(
                'No se pudo descifrar la API key de la máquina "%s". '
                'Revise la llave maestra del sistema.', self.name
            ))

    def set_api_key(self, plain_api_key):
        """Persiste la API key cifrada y metadatos de auditoría."""
        for record in self:
            encrypted = record._encrypt_api_key((plain_api_key or '').strip())
            values = {
                'api_key_encrypted': encrypted,
                'api_key_last4': (plain_api_key or '')[-4:] if plain_api_key else False,
                'api_key_set_at': fields.Datetime.now() if plain_api_key else False,
                'api_key_set_by': self.env.user.id if plain_api_key else False,
            }
            super(VendingMachine, record).write(values)

    def get_api_key(self):
        """Devuelve la API key en texto plano para uso interno de requests."""
        self.ensure_one()
        return self._decrypt_api_key()

    def is_api_key_valid(self, candidate_key):
        """Compara la API key recibida usando comparación segura."""
        self.ensure_one()
        if not candidate_key or not self.api_key_encrypted:
            return False
        expected = self.get_api_key()
        return hmac.compare_digest(expected, candidate_key)

    def _default_invoice_journal(self):
        """
        Retorna el primer diario de ventas de la compañía actual.
        Si el usuario tiene compañía asignada, busca el diario de esa compañía.
        """
        company = self.env.company
        if not company:
            return False
            
        journal = self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', company.id),
        ], limit=1)
        
        return journal.id if journal else False
    
    def _default_customer_location(self):
        """
        Retorna la primera ubicación de cliente disponible.
        """
        location = self.env['stock.location'].search([
            ('usage', '=', 'customer'),
        ], limit=1)
        
        return location.id if location else False
    
    def _default_payment_method(self):
        """
        Retorna el método de pago más apropiado para vending con fallbacks inteligentes.
        Busca en este orden de prioridad:
        1. Método con "QR" en el nombre
        2. Método con "Transferencia" en el nombre
        3. Método con "Efectivo" en el nombre
        4. Cualquier método disponible
        """
        PaymentMethod = self.env['pos.payment.method']
        
        # Prioridad 1: Método con QR (ideal para vending)
        qr_method = PaymentMethod.search([
            ('name', 'ilike', 'QR')
        ], limit=1)
        if qr_method:
            _logger.info(f"[Vending Default] Método de pago por defecto: {qr_method.name} (QR)")
            return qr_method.id
        
        # Prioridad 2: Transferencia
        transfer_method = PaymentMethod.search([
            ('name', 'ilike', 'Transferencia')
        ], limit=1)
        if transfer_method:
            _logger.info(f"[Vending Default] Método de pago por defecto: {transfer_method.name} (Transferencia)")
            return transfer_method.id
        
        # Prioridad 3: Efectivo
        cash_method = PaymentMethod.search([
            ('name', 'ilike', 'Efectivo')
        ], limit=1)
        if cash_method:
            _logger.info(f"[Vending Default] Método de pago por defecto: {cash_method.name} (Efectivo)")
            return cash_method.id
        
        # Prioridad 4: Cualquier método disponible
        any_method = PaymentMethod.search([], limit=1)
        if any_method:
            _logger.warning(f"[Vending Default] Usando método genérico: {any_method.name}")
            return any_method.id
        
        # No hay métodos de pago disponibles - se validará en @api.constrains
        _logger.warning("[Vending Default] No se encontró ningún método de pago disponible")
        return False

    def _default_anonymous_partner(self):
        """
        Busca el partner por defecto para consumidor anónimo.
        Usa la misma lógica que en el controller.
        """
        # Buscar por nombre
        anonymous_partner = self.env['res.partner'].search([
            ('name', 'ilike', 'consumidor final')
        ], limit=1)
        
        if anonymous_partner:
            return anonymous_partner.id
            
        # Buscar cualquier partner con "anónimo" en el nombre
        anonymous_partner = self.env['res.partner'].search([
            ('name', 'ilike', 'anónimo')
        ], limit=1)
        
        if anonymous_partner:
            return anonymous_partner.id
            
        return False

    @api.constrains('code')
    def _check_unique_code(self):
        """
        Valida que el código de la máquina sea único.
        """
        for record in self:
            if not record.code:
                continue
            existing = self.search_count([
                ('code', '=', record.code.strip()),
                ('id', '!=', record.id),
            ])
            if existing:
                raise ValidationError(_(
                    'El código de la máquina debe ser único.'
                ))

    # @api.constrains('code')
    # def _validate_code_format(self):
    #     """
    #     Valida que el código contenga solo números (opcional).
    #     """
    #     import re
    #     for record in self:
    #         if record.code and not re.match(r'^\d+$', record.code.strip()):
    #             raise ValidationError(_(
    #                 'El código debe contener solo números.'
    #             ))

    @api.constrains('warehouse_id')
    def _check_unique_warehouse(self):
        """
        Valida que cada almacén tenga solo una máquina asociada.
        """
        for record in self:
            if not record.warehouse_id:
                continue
            existing = self.search_count([
                ('warehouse_id', '=', record.warehouse_id.id),
                ('id', '!=', record.id),
            ])
            if existing:
                raise ValidationError(_(
                    'Cada almacén solo puede tener una máquina expendedora asociada.'
                ))

    @api.constrains('pos_config_id')
    def _check_unique_pos_config(self):
        """
        Valida que cada punto de venta tenga solo una máquina asociada.
        """
        for record in self:
            if not record.pos_config_id:
                continue
            existing = self.search_count([
                ('pos_config_id', '=', record.pos_config_id.id),
                ('id', '!=', record.id),
            ])
            if existing:
                raise ValidationError(_(
                    'Cada punto de venta solo puede tener una máquina expendedora asociada.'
                ))

    @api.constrains('invoice_journal_id', 'payment_method_id', 'anonymous_partner_id', 'pos_config_id')
    def _check_vending_configuration(self):
        """
        Valida que la configuración de vending esté completa.
        """
        for record in self:
            if not record.invoice_journal_id:
                raise ValidationError(_(
                    'Debe configurar un diario de facturas para la máquina expendedora.'
                ))
            if not record.payment_method_id:
                raise ValidationError(_(
                    'Debe configurar un método de pago para la máquina expendedora.\n\n'
                    'No se encontraron métodos de pago disponibles con los nombres habituales:\n'
                    '• QR (recomendado para vending)\n'
                    '• Transferencia\n'
                    '• Efectivo\n\n'
                    'Por favor, cree un método de pago en: Punto de Venta > Configuración > Métodos de Pago.'
                ))
            if not record.anonymous_partner_id:
                raise ValidationError(_(
                    'Debe configurar un cliente para consumidor anónimo para la máquina expendedora.'
                ))
            if record.invoice_journal_id.type != 'sale':
                raise ValidationError(_(
                    'El diario de facturas debe ser de tipo "Venta".'
                ))
            # Validar que el método de pago esté disponible en el PdV
            if record.pos_config_id and record.payment_method_id:
                if record.payment_method_id not in record.pos_config_id.payment_method_ids:
                    raise ValidationError(_(
                        'El método de pago "%s" debe estar asociado al punto de venta "%s". '
                        'Por favor, agregue este método de pago a la configuración del PdV.',
                        record.payment_method_id.name,
                        record.pos_config_id.name
                    ))

    @api.constrains('pos_config_id', 'warehouse_id', 'invoice_journal_id', 'payment_method_id')
    def _check_company_consistency(self):
        """
        Valida que todos los objetos relacionados pertenezcan a la misma compañía
        que el pos.config de la máquina. Esto previene errores de crossover de compañía
        al crear facturas y movimientos de stock.
        """
        for record in self:
            if not record.pos_config_id:
                continue
            company = record.pos_config_id.company_id
            if not company:
                continue
            errors = []

            # Validar almacén
            if record.warehouse_id and record.warehouse_id.company_id != company:
                errors.append(_(
                    '• Almacén "%(warehouse)s" pertenece a "%(wh_company)s" '
                    'pero el POS pertenece a "%(company)s".',
                    warehouse=record.warehouse_id.name,
                    wh_company=record.warehouse_id.company_id.name,
                    company=company.name,
                ))

            # Validar diario de facturas
            if record.invoice_journal_id and record.invoice_journal_id.company_id != company:
                errors.append(_(
                    '• Diario "%(journal)s" pertenece a "%(j_company)s" '
                    'pero el POS pertenece a "%(company)s".',
                    journal=record.invoice_journal_id.name,
                    j_company=record.invoice_journal_id.company_id.name,
                    company=company.name,
                ))

            # Validar tipo de operación de salida del almacén
            if record.warehouse_id:
                out_type = record.warehouse_id.out_type_id
                if out_type and out_type.company_id and out_type.company_id != company:
                    errors.append(_(
                        '• Tipo de operación "%(op_type)s" del almacén pertenece a "%(op_company)s" '
                        'pero el POS pertenece a "%(company)s".',
                        op_type=out_type.name,
                        op_company=out_type.company_id.name,
                        company=company.name,
                    ))

            if errors:
                raise ValidationError(_(
                    'Inconsistencias de empresa detectadas en la máquina "%(machine)s".\n'
                    'Todos los recursos deben pertenecer a la empresa "%(company)s":\n\n'
                    '%(errors)s\n\n'
                    'Esto causaría errores al generar facturas y movimientos de stock.',
                    machine=record.name or '(nueva)',
                    company=company.name,
                    errors='\n'.join(errors),
                ))

    @api.model_create_multi
    def create(self, vals_list):
        """Sincronizar relación bidireccional POS-Vending al crear."""
        pending_keys = []
        for vals in vals_list:
            pending_keys.append(vals.pop('api_key_input', False))

        records = super().create(vals_list)

        for record, api_key_plain in zip(records, pending_keys):
            if api_key_plain:
                record.set_api_key(api_key_plain)
        
        for record in records:
            if record.pos_config_id and not self.env.context.get('skip_pos_sync'):
                record.pos_config_id.with_context(skip_vending_sync=True).write({
                    'vending_machine_id': record.id
                })
        
        return records

    def write(self, vals):
        """Sincronizar relación bidireccional POS-Vending al escribir."""
        api_key_plain = vals.pop('api_key_input', False) if 'api_key_input' in vals else False

        # Solo sincronizar si no estamos en un contexto de sincronización
        if 'pos_config_id' in vals and not self.env.context.get('skip_pos_sync'):
            # Limpiar referencias anteriores ANTES del write
            for record in self:
                old_pos = self.env['pos.config'].search([
                    ('vending_machine_id', '=', record.id),
                ])
                if old_pos:
                    old_pos.with_context(skip_vending_sync=True).write({'vending_machine_id': False})
        
        result = super().write(vals)
        
        # Establecer nueva referencia DESPUÉS del write
        if 'pos_config_id' in vals and not self.env.context.get('skip_pos_sync'):
            for record in self:
                if record.pos_config_id:
                    record.pos_config_id.with_context(skip_vending_sync=True).write({
                        'vending_machine_id': record.id
                    })

        if api_key_plain:
            self.set_api_key(api_key_plain)
        
        return result
