# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
DUMMY Controller que simula la API de Winfas.

IMPORTANTE: Este archivo es solo para desarrollo y testing.
Eliminar cuando se integre con la API real de Winfas.

Simula los siguientes endpoints de Winfas:
- POST /payments/qr/[machine] - Genera QR de pago
- GET /payments/qr/[uuid] - Retorna imagen del QR  
- POST /status/[reference] - Consulta estado de referencia
"""

import json
import uuid
import hashlib
import logging
import requests
from datetime import datetime, timedelta
from urllib.parse import quote
from odoo import http  # type: ignore
from odoo.http import request  # type: ignore

_logger = logging.getLogger(__name__)
LOG_SEP = "=" * 60

# Almacenamiento en memoria para QRs generados (solo para testing)
# En producción esto lo maneja Winfas
_dummy_qr_storage = {}


class DummyWinfasController(http.Controller):
    """
    Controller DUMMY que simula la API de Winfas.
    
    ELIMINAR este archivo cuando se integre con Winfas real.
    Solo actualizar la URL base en vending_provider_client.py
    """

    def _extract_api_key(self):
        headers = request.httprequest.headers
        for key, value in headers.items():
            if key and key.lower() == 'x-api-key':
                return (value or '').strip()
        return ''

    def _validate_machine_api_key(self, machine_code):
        api_key = self._extract_api_key()
        machine = request.env['vending.machine'].sudo().search([
            ('code', '=', machine_code),
        ], limit=1)

        if not machine:
            return False
        return machine.is_api_key_valid(api_key)

    @http.route(
        '/dummy/payments/qr/<string:machine>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def create_qr(self, machine, **kwargs):
        """
        Simula: POST https://api-v2.winfas.com.ar/payments/qr/[machine]
        
        Request payload:
        {
            "reference": "string",      # Requerido, max 36 chars
            "amount": number,           # Requerido, centavos (ej: 10000 = $100)
            "slot": number,             # Requerido
            "description": "string",    # Opcional, default "Ventas Infas"
            "timeout": number           # Requerido, segundos
        }
        
        Response:
        {
            "url": "string",           # URL de la imagen QR
            "content": "string"        # Contenido del QR
        }
        """
        try:
            _logger.info(f"{LOG_SEP}")
            _logger.info(f"[DUMMY API] === RECIBIDO POST /payments/qr/{machine} ===")

            if not self._validate_machine_api_key(machine):
                return self._error_response('Unauthorized', 401)
            
            body_raw = request.httprequest.get_data(as_text=True)
            _logger.info(f"[DUMMY API] Body: {body_raw[:200]}...")
            
            if not body_raw:
                _logger.error(f"[DUMMY API] Error: body vacío")
                return self._error_response('Request body is required', 400)
            
            try:
                payload = json.loads(body_raw)
            except json.JSONDecodeError:
                _logger.error(f"[DUMMY API] Error: JSON inválido")
                return self._error_response('Invalid JSON format', 400)
            
            # Validar campos requeridos
            validation_error = self._validate_qr_request(payload)
            if validation_error:
                _logger.error(f"[DUMMY API] Error validación: {validation_error}")
                return self._error_response(validation_error, 400)
            
            reference = payload['reference']
            amount = payload['amount']
            slot = payload['slot']
            description = payload.get('description', 'Ventas Infas')
            timeout = payload['timeout']
            
            _logger.info(f"[DUMMY API] Datos recibidos:")
            _logger.info(f"[DUMMY API]   reference={reference}")
            _logger.info(f"[DUMMY API]   amount={amount} (${amount/100:.2f})")
            _logger.info(f"[DUMMY API]   slot={slot}")
            _logger.info(f"[DUMMY API]   timeout={timeout}s")
            
            # Generar UUID único para este QR
            qr_uuid = str(uuid.uuid4())
            _logger.info(f"[DUMMY API] QR UUID generado: {qr_uuid}")
            
            # Crear contenido del QR (simulado - en Winfas sería el deep link de MercadoPago)
            qr_content = self._generate_qr_content(
                machine=machine,
                reference=reference,
                amount=amount,
                slot=slot,
                description=description
            )
            
            # Calcular expiración
            expires_at = datetime.utcnow() + timedelta(seconds=timeout)
            
            # Almacenar en memoria para consultas posteriores
            _dummy_qr_storage[qr_uuid] = {
                'machine': machine,
                'reference': reference,
                'amount': amount,
                'slot': slot,
                'description': description,
                'content': qr_content,
                'created_at': datetime.utcnow().isoformat(),
                'expires_at': expires_at.isoformat(),
                'timeout': timeout,
                'status': 'pending',  # pending, paid, expired
            }
            
            # También indexar por reference para consultas de status
            _dummy_qr_storage[f'ref:{reference}'] = qr_uuid
            
            # Generar URL del QR (apunta a nuestro endpoint dummy)
            base_url = request.httprequest.host_url.rstrip('/')
            qr_url = f"{base_url}/dummy/payments/qr/image/{qr_uuid}"
            
            _logger.info(f"[DUMMY API] ✓ QR generado exitosamente")
            _logger.info(f"[DUMMY API]   url={qr_url}")
            _logger.info(f"[DUMMY API] === FIN DUMMY API ===")
            _logger.info(f"{LOG_SEP}")
            
            response_data = {
                'url': qr_url,
                'content': qr_content,
            }
            
            return request.make_json_response(response_data, status=200)
            
        except Exception as e:
            return self._error_response(f'Internal server error: {str(e)}', 500)

    @http.route(
        '/dummy/payments/qr/image/<string:qr_uuid>',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False
    )
    def get_qr_image(self, qr_uuid, **kwargs):
        """
        Retorna la imagen del QR generado.
        Simula: GET https://api-v2.winfas.com.ar/payments/qr/[uuid]
        
        Genera un QR real usando el servicio de qrserver.com
        """
        qr_data = _dummy_qr_storage.get(qr_uuid)
        
        if not qr_data:
            return request.make_json_response(
                {'error': 'QR not found'},
                status=404
            )
        
        # Verificar expiración
        expires_at = datetime.fromisoformat(qr_data['expires_at'])
        if datetime.utcnow() > expires_at:
            qr_data['status'] = 'expired'
            return request.make_json_response(
                {'error': 'QR expired'},
                status=410
            )
        
        # Redirigir a qrserver.com para generar el QR real
        # En producción, Winfas genera su propia imagen
        qr_content_encoded = quote(qr_data['content'])
        redirect_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={qr_content_encoded}"
        
        return request.redirect(redirect_url, code=302)

    @http.route(
        '/dummy/status/<string:reference>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def get_status(self, reference, **kwargs):
        """
        Simula: POST https://api-v2.winfas.com.ar/status/[reference]
        
        Response:
        {
            "reference": "string",
            "status": "SUCCESS" | "PENDING" | "ERROR" | "EXPIRED"
        }
        """
        qr_uuid = _dummy_qr_storage.get(f'ref:{reference}')
        
        if not qr_uuid:
            return request.make_json_response({
                'reference': reference,
                'status': 'NOT_FOUND'
            }, status=404)
        
        qr_data = _dummy_qr_storage.get(qr_uuid, {})
        machine_code = qr_data.get('machine')
        if not machine_code or not self._validate_machine_api_key(machine_code):
            return self._error_response('Unauthorized', 401)
        
        # Verificar expiración
        expires_at_str = qr_data.get('expires_at')
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.utcnow() > expires_at and qr_data.get('status') == 'pending':
                qr_data['status'] = 'expired'
        
        # Mapear status interno a status de API
        status_map = {
            'pending': 'PENDING',
            'paid': 'SUCCESS',
            'expired': 'EXPIRED',
            'error': 'ERROR',
        }
        
        return request.make_json_response({
            'reference': reference,
            'status': status_map.get(qr_data.get('status', 'pending'), 'PENDING')
        }, status=200)

    @http.route(
        '/dummy/simulate/pay/<string:reference>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def simulate_payment(self, reference, **kwargs):
        """
        ENDPOINT DE TESTING: Simula que un usuario pagó el QR.
        
        Esto es solo para testing manual. En producción, Winfas
        detecta el pago y llama a nuestro webhook.
        
        También dispara automáticamente el webhook de status a Odoo.
        """
        _logger.info(f"{LOG_SEP}")
        _logger.info(f"[SIMULATE PAY] === SIMULANDO PAGO ===")
        _logger.info(f"[SIMULATE PAY] reference={reference}")
        
        qr_uuid = _dummy_qr_storage.get(f'ref:{reference}')
        
        if not qr_uuid:
            _logger.error(f"[SIMULATE PAY] ✗ Reference no encontrada")
            return request.make_json_response({
                'error': 'Reference not found',
                'reference': reference
            }, status=404)
        
        qr_data = _dummy_qr_storage.get(qr_uuid)
        
        if not qr_data:
            _logger.error(f"[SIMULATE PAY] ✗ QR data no encontrada")
            return request.make_json_response({
                'error': 'QR data not found'
            }, status=404)
        
        # Verificar que no esté ya pagado
        if qr_data.get('status') == 'paid':
            _logger.warning(f"[SIMULATE PAY] ✗ Ya estaba pagado")
            return request.make_json_response({
                'error': 'Already paid',
                'reference': reference
            }, status=409)
        
        # Verificar expiración
        expires_at = datetime.fromisoformat(qr_data['expires_at'])
        if datetime.utcnow() > expires_at:
            qr_data['status'] = 'expired'
            _logger.warning(f"[SIMULATE PAY] ✗ QR expirado")
            return request.make_json_response({
                'error': 'QR expired',
                'reference': reference
            }, status=410)
        
        # Marcar como pagado
        qr_data['status'] = 'paid'
        qr_data['paid_at'] = datetime.utcnow().isoformat()
        _logger.info(f"[SIMULATE PAY] ✓ Marcado como pagado")
        
        # Simular llamada al webhook de Odoo (internamente)
        # En producción, Winfas haría un POST HTTP a nuestro webhook
        _logger.info(f"[SIMULATE PAY] Disparando webhook interno...")
        webhook_triggered = self._trigger_webhook(reference, 'SUCCESS')
        _logger.info(f"[SIMULATE PAY] Webhook resultado: {webhook_triggered}")
        _logger.info(f"[SIMULATE PAY] === FIN SIMULACIÓN ===")
        _logger.info(f"{LOG_SEP}")
        
        return request.make_json_response({
            'success': True,
            'reference': reference,
            'status': 'paid',
            'paid_at': qr_data['paid_at'],
            'webhook_triggered': webhook_triggered,
            'message': 'Payment simulated successfully. Webhook triggered to update order.'
        }, status=200)

    @http.route(
        '/dummy/simulate/error/<string:reference>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def simulate_error(self, reference, **kwargs):
        """
        ENDPOINT DE TESTING: Simula un error de entrega.
        
        Dispara webhook con status ERROR.
        """
        qr_uuid = _dummy_qr_storage.get(f'ref:{reference}')
        
        if not qr_uuid:
            return request.make_json_response({
                'error': 'Reference not found'
            }, status=404)
        
        qr_data = _dummy_qr_storage.get(qr_uuid)
        if qr_data:
            qr_data['status'] = 'error'
        
        # Leer descripción del error del body si existe
        try:
            body_raw = request.httprequest.get_data(as_text=True)
            error_desc = json.loads(body_raw).get('description', 'Simulated error') if body_raw else 'Simulated error'
        except Exception:
            error_desc = 'Simulated error'
        
        webhook_triggered = self._trigger_webhook(reference, 'ERROR', error_desc)
        
        return request.make_json_response({
            'success': True,
            'reference': reference,
            'status': 'error',
            'webhook_triggered': webhook_triggered
        }, status=200)

    def _trigger_webhook(self, reference, status, description=None):
        """
        Dispara internamente el webhook de status a Odoo.
        
        En producción, Winfas haría un HTTP POST externo.
        Aquí lo hacemos internamente para testing.
        """
        _logger.info(f"[WEBHOOK INTERNO] === DISPARANDO WEBHOOK ===")
        _logger.info(f"[WEBHOOK INTERNO] reference={reference}, status={status}")
        
        try:
            order = request.env['pos.order'].sudo().search([
                ('vending_reference', '=', reference)
            ], limit=1)

            if not order:
                _logger.error(f"[WEBHOOK INTERNO] ✗ Orden no encontrada con reference={reference}")
                return False

            machine = order.vending_machine_id
            if not machine:
                _logger.error(f"[WEBHOOK INTERNO] ✗ Orden sin máquina asociada")
                return False

            api_key = machine.get_api_key()
            if not api_key:
                _logger.error(f"[WEBHOOK INTERNO] ✗ Máquina sin API key configurada")
                return False

            base_url = request.httprequest.host_url.rstrip('/')
            headers = {
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'X-Odoo-Database': request.db,
            }

            if status == 'SUCCESS':
                payment_response = requests.post(
                    f'{base_url}/v1/vending/webhook/payment_status',
                    json={
                        'reference': reference,
                        'status': 'APPROVED',
                        'description': description or 'DUMMY_PAYMENT_APPROVED',
                    },
                    headers=headers,
                    timeout=10,
                )
                if payment_response.status_code != 200:
                    _logger.error('[WEBHOOK INTERNO] ✗ payment_status falló HTTP %s', payment_response.status_code)
                    return False

                delivery_response = requests.post(
                    f'{base_url}/v1/vending/webhook/delivery_status',
                    json={
                        'reference': reference,
                        'status': 'SUCCESS',
                        'description': description or 'DUMMY_DELIVERY_SUCCESS',
                    },
                    headers=headers,
                    timeout=10,
                )
                return delivery_response.status_code == 200

            delivery_response = requests.post(
                f'{base_url}/v1/vending/webhook/delivery_status',
                json={
                    'reference': reference,
                    'status': 'ERROR',
                    'description': description or 'DUMMY_DELIVERY_ERROR',
                },
                headers=headers,
                timeout=10,
            )
            return delivery_response.status_code == 200

        except Exception as error:
            _logger.error(f"[WEBHOOK INTERNO] ✗ Error: {error}")
            return False

    def _validate_qr_request(self, payload):
        """
        Valida el payload del request de creación de QR.
        
        Returns:
            str: Mensaje de error si hay, None si es válido
        """
        # Validar reference
        reference = payload.get('reference')
        if not reference:
            return 'reference is required'
        if not isinstance(reference, str) or len(reference) > 36:
            return 'reference must be a string with max 36 characters'
        
        # Validar amount
        amount = payload.get('amount')
        if amount is None:
            return 'amount is required'
        if not isinstance(amount, (int, float)) or amount <= 0:
            return 'amount must be a positive number'
        
        # Validar slot
        slot = payload.get('slot')
        if slot is None:
            return 'slot is required'
        if not isinstance(slot, (int, float)):
            return 'slot must be a number'
        
        # Validar timeout
        timeout = payload.get('timeout')
        if timeout is None:
            return 'timeout is required'
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return 'timeout must be a positive number'
        
        return None

    def _generate_qr_content(self, machine, reference, amount, slot, description):
        """
        Genera el contenido del QR.
        
        En producción, Winfas genera un deep link de MercadoPago.
        Aquí generamos un contenido de testing.
        """
        # Crear un hash para simular el token de pago
        content_base = f"{machine}:{reference}:{amount}:{slot}"
        token = hashlib.sha256(content_base.encode()).hexdigest()[:16]
        
        # Simular formato de deep link de MercadoPago
        # En producción sería algo como: https://link.mercadopago.com.ar/...
        return f"https://dummy-payment.test/pay?ref={reference}&token={token}&amount={amount}"

    def _error_response(self, message, status_code):
        """Helper para respuestas de error."""
        return request.make_json_response({
            'error': message
        }, status=status_code)


# Función de utilidad para limpiar QRs expirados (opcional, para mantenimiento)
def cleanup_expired_qrs():
    """Limpia QRs expirados del storage en memoria."""
    now = datetime.utcnow()
    expired_uuids = []
    
    for key, value in list(_dummy_qr_storage.items()):
        if key.startswith('ref:'):
            continue
        if isinstance(value, dict) and 'expires_at' in value:
            expires_at = datetime.fromisoformat(value['expires_at'])
            if now > expires_at:
                expired_uuids.append(key)
    
    for qr_uuid in expired_uuids:
        qr_data = _dummy_qr_storage.pop(qr_uuid, None)
        if qr_data and 'reference' in qr_data:
            _dummy_qr_storage.pop(f"ref:{qr_data['reference']}", None)
    
    return len(expired_uuids)
