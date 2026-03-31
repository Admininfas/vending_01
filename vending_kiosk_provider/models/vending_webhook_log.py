# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Modelo para logging de webhooks de vending machines.

Persiste los requests recibidos en los endpoints de webhooks
y el resultado de su procesamiento para auditoría.
"""

import json
from datetime import timedelta
from odoo import api, fields, models  # type: ignore

import logging
_logger = logging.getLogger(__name__)


class VendingWebhookLog(models.Model):
    """Log de webhooks recibidos desde proveedores de vending machines."""
    
    _name = 'vending.webhook.log'
    _description = 'Vending Webhook Log'
    _order = 'create_date desc'
    _rec_name = 'display_name'

    # ── Campos principales ──
    endpoint = fields.Selection([
        ('payment_status', 'Payment Status Webhook'),
        ('delivery_status', 'Delivery Status Webhook'),
        ('load', 'Load Webhook'),
        ('alarm', 'Alarm Webhook'),
    ], string='Endpoint', required=True, index=True, help='Tipo de webhook recibido')
    
    http_method = fields.Char(
        string='HTTP Method', 
        size=10, 
        default='POST',
        help='Método HTTP utilizado (POST, GET, etc.)'
    )

    reference = fields.Char(
        string='Reference',
        size=36,
        index=True,
        help='Referencia de la transacción extraída del payload (para búsquedas rápidas)'
    )
    
    # ── Headers y payload ──
    headers_json = fields.Text(
        string='Headers JSON',
        help='Subset de headers HTTP en formato JSON'
    )
    
    payload_json = fields.Text(
        string='Payload JSON', 
        help='Body del request tal como se recibió'
    )
    
    is_json = fields.Boolean(
        string='Is Valid JSON',
        default=False,
        help='Indica si el payload era JSON válido'
    )
    
    # ── Response info ──
    status_code = fields.Integer(
        string='Status Code',
        help='Código HTTP devuelto (200, 400, 500, etc.)'
    )
    
    warnings = fields.Text(
        string='Warnings JSON',
        default='[]',
        help='Lista de warnings de validación en formato JSON'
    )
    
    error_message = fields.Text(
        string='Error Message',
        help='Mensaje de error si status_code >= 400'
    )

    # ── Auditoría liviana ──
    processing_result = fields.Selection([
        ('processed', 'Procesado'),
        ('partial', 'Procesado parcial'),
        ('duplicate', 'Duplicado'),
        ('late_arrival', 'Extemporáneo'),
        ('order_not_found', 'Orden no encontrada'),
        ('auth_error', 'Error de autenticación'),
        ('validation_error', 'Error de validación'),
        ('internal_error', 'Error interno'),
    ], string='Resultado', index=True,
       help='Qué decidió Odoo hacer con este webhook')

    actions_json = fields.Text(
        string='Acciones',
        help='Resumen de acciones ejecutadas por Odoo (JSON compacto)'
    )

    # ── Campos computados para la vista ──
    display_name = fields.Char(
        string='Display Name',
        compute='_compute_display_name',
        store=True
    )
    
    warnings_count = fields.Integer(
        string='Warnings Count',
        compute='_compute_warnings_count',
        store=False
    )
    
    formatted_headers = fields.Text(
        string='Formatted Headers',
        compute='_compute_formatted_headers',
        store=False
    )
    
    formatted_payload = fields.Text(
        string='Formatted Payload', 
        compute='_compute_formatted_payload',
        store=False
    )

    @api.depends('endpoint', 'create_date', 'processing_result')
    def _compute_display_name(self):
        """Genera nombre para mostrar en vistas."""
        result_labels = dict(self._fields['processing_result'].selection or [])
        for record in self:
            endpoint_name = dict(self._fields['endpoint'].selection).get(record.endpoint, 'Unknown')
            date_str = record.create_date.strftime('%Y-%m-%d %H:%M:%S') if record.create_date else 'No Date'
            result_label = result_labels.get(record.processing_result, '') if record.processing_result else ''
            suffix = f" - {result_label}" if result_label else f" ({record.status_code or 0})"
            record.display_name = f"{endpoint_name} - {date_str}{suffix}"
    
    @api.depends('warnings')
    def _compute_warnings_count(self):
        """Calcula número de warnings."""
        for record in self:
            try:
                warnings_list = json.loads(record.warnings or '[]')
                record.warnings_count = len(warnings_list) if isinstance(warnings_list, list) else 0
            except (json.JSONDecodeError, TypeError):
                record.warnings_count = 0
    
    @api.depends('headers_json')
    def _compute_formatted_headers(self):
        """Formatea headers para mejor visualización."""
        for record in self:
            try:
                headers_dict = json.loads(record.headers_json or '{}')
                record.formatted_headers = json.dumps(headers_dict, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                record.formatted_headers = record.headers_json or ''
    
    @api.depends('payload_json')
    def _compute_formatted_payload(self):
        """Formatea payload para mejor visualización."""
        for record in self:
            if not record.is_json:
                record.formatted_payload = record.payload_json or ''
                continue
            
            try:
                payload_dict = json.loads(record.payload_json or '{}')
                record.formatted_payload = json.dumps(payload_dict, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                record.formatted_payload = record.payload_json or ''

    def get_warnings_list(self):
        """
        Retorna la lista de warnings parseada.
        
        Returns:
            list: Lista de strings con los warnings, [] si no hay
        """
        try:
            return json.loads(self.warnings or '[]')
        except (json.JSONDecodeError, TypeError):
            return []
    
    def add_warning(self, warning_code):
        """
        Agrega un warning a la lista existente.
        
        Args:
            warning_code (str): Código del warning a agregar
        """
        current_warnings = self.get_warnings_list()
        if warning_code not in current_warnings:
            current_warnings.append(warning_code)
            self.warnings = json.dumps(current_warnings, ensure_ascii=False)

    @api.model
    def _cron_cleanup_old_logs(self, days=7):
        """Elimina logs de webhook con antigüedad mayor a `days` días."""
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old_logs = self.search([('create_date', '<', cutoff)])
        count = len(old_logs)
        old_logs.unlink()
        _logger.info('Webhook log cleanup: %d registros eliminados (anteriores a %s).', count, cutoff)