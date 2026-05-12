# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Controller para creación de órdenes de vending y solicitud de QR.

Endpoints:
- POST /v1/vending/qr/create - Crea orden y solicita QR al proveedor
- POST /v1/vending/order/status - Consulta estado de una orden (polling)
"""

import uuid
import hashlib
import logging
from odoo import http, fields  # type: ignore
from odoo.http import request  # type: ignore
from odoo.tools import html2plaintext  # type: ignore

_logger = logging.getLogger(__name__)

# Separador visual para logs
LOG_SEP = "=" * 60


class VendingQrController(http.Controller):
    """Controller for creating draft POS orders and requesting QR from provider."""

    @staticmethod
    def _to_public_description_text(description_html):
        """Convierte HTML de Odoo a texto plano para frontend kiosk."""
        if not description_html:
            return False

        text = html2plaintext(description_html or '').strip()
        return text or False

    @staticmethod
    def _get_pricelist_price(pricelist, product_tmpl):
        """Obtiene el precio de visualización según la pricelist del POS."""
        if not product_tmpl:
            return 0.0

        product_variant = product_tmpl.product_variant_id
        price = product_tmpl.list_price or 0.0
        if not pricelist or not product_variant:
            return float(price)

        get_price = getattr(pricelist, 'get_product_price', None)
        if callable(get_price):
            return float(get_price(product_variant, 1.0, False) or 0.0)

        get_price = getattr(pricelist, '_get_product_price', None)
        if callable(get_price):
            return float(get_price(product_variant, 1.0, False) or 0.0)

        return float(price)

    def _build_product_meta_for_poll(self, config, product_ids, product_slots, product_min_slot_code):
        """Construye metadata mínima de productos para refresco en vivo del kiosk."""
        product_meta = {}
        if not product_ids:
            return product_meta

        products = request.env['product.template'].sudo().browse(product_ids).exists()
        products_by_id = {product.id: product for product in products}

        for product_id in product_ids:
            product = products_by_id.get(product_id)
            if not product:
                continue

            write_date = product.write_date
            write_date_text = fields.Datetime.to_string(write_date) if write_date else False
            product_meta[product_id] = {
                'id': product.id,
                'display_name': product.display_name or product.name or '',
                'public_description': self._to_public_description_text(product.public_description),
                'write_date': write_date_text,
                'price': self._get_pricelist_price(config.pricelist_id, product),
                'min_slot_code': product_min_slot_code.get(product_id),
                'slots': product_slots.get(product_id, []),
            }

        return product_meta

    def _find_anonymous_consumer_partner(self, machine):
        """
        Obtiene el partner anónimo configurado en la máquina.
        Si no está configurado, usa la lógica de búsqueda por defecto.
        """
        env = request.env
        
        # Si la máquina tiene un partner configurado, usarlo
        if machine.anonymous_partner_id:
            _logger.info(f"[QR CREATE] ✓ Cliente anónimo configurado en máquina: ID={machine.anonymous_partner_id.id}")
            return machine.anonymous_partner_id
            
        # Fallback: buscar por nombre (lógica anterior)
        partner = env['res.partner'].sudo().search([
            ('name', '=', 'Consumidor Final Anónimo')
        ], limit=1)
        
        if not partner:
            _logger.error("[QR CREATE] Partner 'Consumidor Final Anónimo' no encontrado y máquina no tiene partner configurado")
            return None
            
        # Verificar el tipo de responsabilidad fiscal
        if hasattr(partner, 'l10n_ar_afip_responsibility_type_id') and partner.l10n_ar_afip_responsibility_type_id:
            responsibility_name = partner.l10n_ar_afip_responsibility_type_id.name
            if responsibility_name != 'Consumidor Final':
                _logger.error(
                    f"[QR CREATE] Partner 'Consumidor Final Anónimo' tiene responsabilidad '{responsibility_name}', "
                    f"se esperaba 'Consumidor Final'"
                )
                return None
        else:
            _logger.error("[QR CREATE] Partner 'Consumidor Final Anónimo' no tiene tipo de responsabilidad fiscal configurado")
            return None
            
        _logger.info(f"[QR CREATE] ✓ Cliente anónimo encontrado (fallback): ID={partner.id}")
        return partner

    @http.route('/v1/vending/order/status', type='jsonrpc', auth='public', csrf=False)
    def get_order_status(self, **kwargs):
        """
        Polling endpoint para consultar el estado de una orden de vending.
        
        Request body:
        {
            "reference": "string"  // vending_reference de la orden
        }
        
        Response:
        {
            "status": "draft" | "qr_ready" | "qr_expired" | "payment_error" |
                      "payment_success" | "vending_delivery_error" | "vending_delivery_success",
            "found": true | false
        }
        """
        # En Odoo 19, los parámetros JSON vienen en kwargs directamente
        reference = kwargs.get('reference')
        
        _logger.info(f"{LOG_SEP}")
        _logger.info(f"[POLLING] Consultando estado de orden")
        _logger.info(f"[POLLING] Reference: {reference}")
        
        if not reference:
            _logger.warning(f"[POLLING] Error: reference no proporcionada")
            return {'error': 'reference is required', 'found': False}
        
        env = request.env
        order = env['pos.order'].sudo().search([
            ('vending_reference', '=', reference)
        ], limit=1)
        
        if not order:
            _logger.info(f"[POLLING] Orden no encontrada para reference={reference} (posiblemente eliminada)")
            return {'found': False, 'status': None}
        
        _logger.info(f"[POLLING] Orden encontrada: id={order.id}, status={order.vending_status}")

        # Verificar si el QR ha expirado automáticamente
        if order.vending_status == 'qr_ready' and order._is_qr_expired():
            _logger.info(f"[POLLING] QR expirado detectado para {reference}, marcando como expirado")
            order.mark_as_qr_expired()

        status = order.vending_status
        _logger.info(f"[POLLING] vending_status={status}, vending_error_description={order.vending_error_description}")
        
        status_map = {
            'payment_success': 'payment_success',
            'vending_delivery_success': 'success',
            'payment_error': 'error',
            'vending_delivery_error': 'error',
            'qr_expired': 'error',
            'user_cancelled': 'error',
        }

        # Determinar error_type si es un error FINAL
        error_type = None
        error_type_map = {
            'payment_error': 'payment',
            'vending_delivery_error': 'delivery',
            'qr_expired': 'timeout',
            'user_cancelled': 'cancelled',
        }
        error_type = error_type_map.get(status)

        response = {
            'found': True,
            'status': status_map.get(status, status),
            'order_id': order.id,
            'vending_status': status,
        }

        # Agregar error_type, error_type_label y error_description SOLO si hay error
        if error_type:
            response['error_type'] = error_type
            error_type_labels = {
                'payment': 'Ocurrió un Error',
                'delivery': 'Ocurrió un Error',
                'timeout': 'QR Expirado',
                'cancelled': 'Ocurrió un Error',
            }
            response['error_type_label'] = error_type_labels.get(error_type, 'Ocurrió un Error')
            response['error_description'] = order.vending_error_description or 'Ocurrió un error desconocido'
            _logger.info(f"[POLLING] Response con error: {response}")

        return response

    @http.route('/v1/vending/qr/create', type='jsonrpc', auth='public', csrf=False)
    def create_qr(self, **kwargs):
        """
        Crea una orden POS draft y solicita QR de pago al proveedor.
        
        Request body:
        {
            "product_id": number,      // ID del product.template
            "pos_config_id": number,   // ID del pos.config
            "description": "string"    // Opcional, descripción para el QR
        }
        
        Response:
        {
            "reference": "string",
            "order_id": number,
            "amount_cents": number,
            "qr": {
                "url": "string",
                "content": "string",
                "timeout": number
            }
        }
        """
        # En Odoo 19, los parámetros JSON vienen en kwargs directamente
        product_id = kwargs.get('product_id')
        pos_config_id = kwargs.get('pos_config_id')
        description = kwargs.get('description')

        _logger.info(f"{LOG_SEP}")
        _logger.info(f"[QR CREATE] === INICIO CREACIÓN DE ORDEN Y QR ===")
        _logger.info(f"[QR CREATE] product_id={product_id}, pos_config_id={pos_config_id}")
        _logger.info(f"[QR CREATE] description={description}")

        # Validaciones básicas
        if not product_id or not pos_config_id:
            _logger.error(f"[QR CREATE] Error: faltan parámetros requeridos")
            return {'error': 'Error en la solicitud. Por favor, intente nuevamente.'}

        try:
            product_id = int(product_id)
            pos_config_id = int(pos_config_id)
        except (ValueError, TypeError):
            _logger.error(f"[QR CREATE] Error: parámetros con formato inválido")
            return {'error': 'Error en la solicitud. Por favor, intente nuevamente.'}

        env = request.env
        
        # Validar producto
        product_tmpl = env['product.template'].sudo().browse(product_id).exists()
        if not product_tmpl:
            return {'error': 'El producto seleccionado no existe. Por favor, seleccione otro producto.'}

        # Validar POS config
        pos_config = env['pos.config'].sudo().browse(pos_config_id).exists()
        if not pos_config:
            return {'error': 'La configuración del punto de venta no es válida. Contacte al administrador.'}

        # Validar máquina vending
        machine = pos_config.vending_machine_id
        if not machine:
            return {'error': 'La máquina expendedora no está configurada correctamente. Contacte al administrador.'}

        if machine.is_fault_blocked:
            return {
                'error': 'La máquina expendedora está desactivada por falla.',
                'error_code': 'MACHINE_DISABLED',
            }

        # Validar que el POS tenga sesión activa
        if not pos_config.current_session_id:
            _logger.error(f"[QR CREATE] POS config {pos_config.id} no tiene sesión activa")
            return {'error': 'El punto de venta no tiene una sesión activa. Contacte al administrador.'}

        # Buscar el mejor slot para el producto (con mayor stock)
        slot = pos_config.get_best_slot_for_product(product_tmpl.id)
        
        _logger.info(f"[QR CREATE] Buscando mejor slot para machine_id={machine.id}, product_tmpl_id={product_tmpl.id}")
        if not slot:
            _logger.error(f"[QR CREATE] ✗ Slot no encontrado o sin stock")
            # Debug: mostrar todos los slots disponibles
            all_slots = env['vending.slot'].sudo().search([('machine_id', '=', machine.id)])
            _logger.info(f"[QR CREATE] Slots disponibles en máquina {machine.id}:")
            for s in all_slots:
                _logger.info(f"[QR CREATE]   - Slot {s.code}: product_tmpl_id={s.product_tmpl_id.id}, is_active={s.is_active}, stock={s.current_stock}, location_id={s.location_id.id if s.location_id else 'None'}")
            return {'error': f'El producto "{product_tmpl.display_name}" no está disponible en esta máquina. Por favor, seleccione otro producto.'}
        
        _logger.info(f"[QR CREATE] ✓ Mejor slot encontrado: {slot.code}, stock={slot.current_stock}, is_active={slot.is_active}")

        if slot.is_fault_blocked:
            _logger.error(f"[QR CREATE] ✗ Slot bloqueado por falla: slot={slot.code}")
            return {
                'error': f'El slot "{slot.name}" está desactivado por falla. Por favor, seleccione otro producto.',
                'error_code': 'SLOT_DISABLED',
            }

        # Verificar que el slot tenga stock disponible
        if not slot.current_stock or not slot.is_active:
            _logger.error(f"[QR CREATE] ✗ Sin stock: current_stock={slot.current_stock}, is_active={slot.is_active}")
            return {'error': f'Lo sentimos, el producto "{product_tmpl.display_name}" no tiene stock disponible en este momento. Por favor, seleccione otro producto.'}

        # Obtener variante del producto
        product_variant = product_tmpl.product_variant_id

        # Calcular precio con pricelist
        price = product_tmpl.list_price
        pricelist = pos_config.pricelist_id
        if pricelist and product_variant:
            get_price = getattr(pricelist, 'get_product_price', None)
            if callable(get_price):
                price = get_price(product_variant, 1.0, False)
            else:
                get_price = getattr(pricelist, '_get_product_price', None)
                if callable(get_price):
                    price = get_price(product_variant, 1.0, False)

        # Calcular impuestos
        taxes = product_tmpl.taxes_id.compute_all(
            price,
            currency=pos_config.currency_id,
            quantity=1.0,
            product=product_variant,
            partner=False,
        )
        total_included = taxes.get('total_included', price)
        total_excluded = taxes.get('total_excluded', price)
        amount_cents = int(round(total_included * 100))

        # Buscar cliente consumidor anónimo
        anonymous_partner = self._find_anonymous_consumer_partner(machine)
        if not anonymous_partner:
            return {
                'error': 'Error de configuración del sistema vending. '
                         'Contacte al administrador para configurar el cliente consumidor anónimo '
                         'en la máquina expendedora.'
            }
        
        # Generar referencia única
        reference = uuid.uuid4().hex[:32]  # Max 32 chars para dejar margen
        description = description or product_tmpl.display_name or 'Ventas Infas'
        
        # Crear orden POS
        order_vals = {
            'name': reference,
            'pos_reference': reference,
            'state': 'draft',
            'config_id': pos_config.id,
            'company_id': pos_config.company_id.id,
            'pricelist_id': pos_config.pricelist_id.id,
            'currency_id': pos_config.currency_id.id,
            'partner_id': anonymous_partner.id,
            'amount_total': total_included,
            'amount_tax': total_included - total_excluded,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'session_id': pos_config.current_session_id.id if pos_config.current_session_id else False,
            'vending_reference': reference,
            'vending_machine_id': machine.id,
            'vending_slot_id': slot.id,
            'vending_status': 'draft',
        }
        
        _logger.info(f"[QR CREATE] Creando pos.order con reference={reference}")
        order = env['pos.order'].sudo().create(order_vals)
        _logger.info(f"[QR CREATE] ✓ Orden creada: id={order.id}")
        
        # Crear línea de orden
        line_vals = {
            'order_id': order.id,
            'product_id': product_variant.id,
            'name': product_tmpl.display_name,
            'qty': 1,
            'price_unit': price,
            'price_subtotal': total_excluded,
            'price_subtotal_incl': total_included,
            'tax_ids': [(6, 0, product_tmpl.taxes_id.ids)],
        }
        env['pos.order.line'].sudo().create(line_vals)
        _logger.info(f"[QR CREATE] ✓ Línea de orden creada")

        # Solicitar QR al proveedor
        _logger.info(f"[QR CREATE] Solicitando QR al proveedor...")
        _logger.info(f"[QR CREATE] -> machine={machine.code}, slot={slot.code}, amount={amount_cents}")
        qr_timeout = pos_config.vending_qr_timeout_seconds or 120
        _logger.info(f"[QR CREATE] -> timeout={qr_timeout}s (desde configuración)")
        try:
            provider = env['vending.provider.client'].sudo()
            qr_data = provider.request_qr(
                machine_identifier=machine.code,
                reference=reference,
                amount_cents=amount_cents,
                slot_number=slot.code,
                description=description,
                timeout=qr_timeout,
            )
            _logger.info(f"[QR CREATE] ✓ QR recibido del proveedor")
            _logger.info(f"[QR CREATE] -> url={qr_data.get('url', '')[:50]}...")
        except Exception as e:
            _logger.error(f"[QR CREATE] ✗ Error solicitando QR: {e}")
            order.write({'vending_status': 'payment_error'})
            return {'error': 'No se pudo generar el código QR de pago. Por favor, intente nuevamente o contacte al administrador.'}

        # Actualizar orden con estado del QR
        order.write({'vending_status': 'qr_ready'})

        _logger.info(f"[QR CREATE] ✓ Orden actualizada a status=qr_ready")
        _logger.info(f"[QR CREATE] === FIN - Orden {order.id} lista con QR ==")
        _logger.info(f"{LOG_SEP}")

        return {
            'reference': reference,
            'order_id': order.id,
            'amount_cents': amount_cents,
            'qr': qr_data,
            'slot_code': slot.code,
            'slot_name': slot.name,
        }

    @http.route('/v1/vending/products/poll', type='jsonrpc', auth='public', csrf=False)
    def poll_products(self, **kwargs):
        """
        Lightweight polling endpoint for product availability updates.

        The client sends a hash of its current product state.  If the server
        computes a *different* hash it replies with the full updated data so
        the kiosk can refresh its catalogue without a full page reload.

        Request body:
        {
            "pos_config_id": number,
            "current_hash": "string"   // hash the client computed last time
        }

        Response:
        {
            "changed": true | false,
            "hash": "string",                    // always present
            "product_ids": [int, ...] | null,    // only when changed
            "product_slots": {id: [...]} | null  // only when changed
        }
        """
        config_id = kwargs.get('pos_config_id')
        client_hash = kwargs.get('current_hash', '')

        if not config_id:
            return {'error': 'pos_config_id is required'}

        env = request.env
        config = env['pos.config'].sudo().browse(int(config_id))

        if not config.exists() or not config.vending_machine_id:
            return {
                'changed': False,
                'hash': '',
                'product_ids': None,
                'product_slots': None,
                'product_min_slot_code': None,
            }

        catalog_data = config.get_vending_catalog_data()
        product_ids = catalog_data['product_ids']
        product_slots = catalog_data['product_slots']
        product_min_slot_code = catalog_data['product_min_slot_code']
        machine = config.vending_machine_id
        machine_fault_blocked = bool(machine.is_fault_blocked)
        machine_has_fault_blocked_slots = bool(machine.has_fault_blocked_slots)
        machine_fault_blocked_slots_count = machine.fault_blocked_slots_count or 0
        kiosk_refresh_token = int(getattr(machine, 'kiosk_refresh_token', 0) or 0)
        product_meta = self._build_product_meta_for_poll(
            config,
            product_ids,
            product_slots,
            product_min_slot_code,
        )

        # Build a deterministic hash from product ids + slots + catalog metadata.
        raw = str(sorted(product_ids)) + str(sorted(
            (
                int(key),
                tuple(sorted((slot['code'], slot['name'], slot['stock']) for slot in value))
            )
            for key, value in product_slots.items()
        )) + str(sorted(
            (
                int(key),
                value.get('display_name') or '',
                value.get('public_description') or '',
                round(float(value.get('price') or 0.0), 6),
                value.get('write_date') or '',
                value.get('min_slot_code') or 0,
            )
            for key, value in product_meta.items()
        )) + str((machine_fault_blocked, machine_has_fault_blocked_slots, machine_fault_blocked_slots_count, kiosk_refresh_token))
        server_hash = hashlib.md5(raw.encode()).hexdigest()

        if server_hash == client_hash:
            return {
                'changed': False,
                'hash': server_hash,
                'product_ids': None,
                'product_slots': None,
                'product_min_slot_code': None,
                'product_meta': None,
                'machine_fault_blocked': machine_fault_blocked,
                'machine_has_fault_blocked_slots': machine_has_fault_blocked_slots,
                'machine_fault_blocked_slots_count': machine_fault_blocked_slots_count,
                'kiosk_refresh_token': kiosk_refresh_token,
            }

        return {
            'changed': True,
            'hash': server_hash,
            'product_ids': product_ids,
            'product_slots': product_slots,
            'product_min_slot_code': product_min_slot_code,
            'product_meta': product_meta,
            'machine_fault_blocked': machine_fault_blocked,
            'machine_has_fault_blocked_slots': machine_has_fault_blocked_slots,
            'machine_fault_blocked_slots_count': machine_fault_blocked_slots_count,
        }
