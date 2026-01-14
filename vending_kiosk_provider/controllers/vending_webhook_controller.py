# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Controller para webhooks DUMMY de vending machines.

Implementa 2 endpoints públicos:
- POST /v1/vending/webhook/status: Estado de transacciones
- POST /v1/vending/webhook/load: Información de carga de stock

Ambos endpoints son DUMMY (no implementan lógica de negocio real)
y están diseñados para testing y desarrollo.
"""

import json
from datetime import datetime
from odoo import http  # type: ignore
from odoo.http import request  # type: ignore


class VendingWebhookController(http.Controller):
    """Controller DUMMY para webhooks de vending machines."""

    @http.route('/v1/vending/webhook/status', type='http', auth='public', methods=['POST'], csrf=False)
    def webhook_status(self, **kwargs):
        """
        Webhook DUMMY para recibir actualizaciones de estado de transacciones.
        
        Payload esperado:
        {
            "reference": "string",
            "status": "SUCCESS" | "ERROR", 
            "description": "string" (opcional)
        }
        
        Returns:
            JSON response con received_at, mode=dummy, y warnings de validación
        """
        return self._process_webhook('status', request)

    @http.route('/v1/vending/webhook/load', type='http', auth='public', methods=['POST'], csrf=False)  
    def webhook_load(self, **kwargs):
        """
        Webhook DUMMY para recibir información de carga de stock en slots.
        
        Payload esperado:
        {
            "machine": "string",
            "slot": "string", 
            "quantity": number
        }
        
        Returns:
            JSON response con received_at, mode=dummy, y warnings de validación
        """
        return self._process_webhook('load', request)

    def _process_webhook(self, endpoint_name, request_obj):
        """
        Procesa un webhook de forma unificada.
        
        Args:
            endpoint_name (str): 'status' o 'load'
            request_obj: Objeto request de Odoo
            
        Returns:
            JSON response con logging automático
        """
        received_at = datetime.utcnow().isoformat() + 'Z'
        log_data = {
            'endpoint': endpoint_name,
            'http_method': request_obj.httprequest.method,
            'remote_addr': request_obj.httprequest.environ.get('REMOTE_ADDR', ''),
            'status_code': 200,  # Default, puede cambiar
            'warnings': '[]',
            'is_json': False,
        }
        
        try:
            # Capturar headers relevantes
            headers_subset = self._extract_headers(request_obj.httprequest.headers)
            log_data['headers_json'] = json.dumps(headers_subset, ensure_ascii=False)
            
            # Leer y parsear body
            body_raw = request_obj.httprequest.get_data(as_text=True)
            log_data['payload_json'] = body_raw
            
            try:
                payload_dict = json.loads(body_raw) if body_raw else {}
                log_data['is_json'] = True
            except json.JSONDecodeError:
                # JSON inválido -> HTTP 400
                log_data['status_code'] = 400
                log_data['error_message'] = 'Invalid JSON in request body'
                self._create_log(log_data)
                
                return request.make_json_response({
                    'error': 'Invalid JSON format',
                    'received_at': received_at,
                    'mode': 'dummy'
                }, status=400)
            
            # Validar payload según endpoint 
            warnings = self._validate_payload(endpoint_name, payload_dict)
            log_data['warnings'] = json.dumps(warnings, ensure_ascii=False)
            
            # Crear log exitoso
            self._create_log(log_data)
            
            # Respuesta exitosa
            return request.make_json_response({
                'received_at': received_at,
                'mode': 'dummy',
                'warnings': warnings
            }, status=200)
            
        except Exception as e:
            # Error interno del servidor -> HTTP 500
            log_data['status_code'] = 500
            log_data['error_message'] = f'Internal server error: {str(e)}'
            self._create_log(log_data)
            
            return request.make_json_response({
                'error': 'Internal server error',
                'received_at': received_at,
                'mode': 'dummy'
            }, status=500)

    def _extract_headers(self, headers):
        """
        Extrae un subset de headers relevantes para logging.
        
        Args:
            headers: Headers del request HTTP
            
        Returns:
            dict: Headers filtrados
        """
        relevant_headers = ['Content-Type', 'User-Agent', 'Authorization', 'X-Forwarded-For']
        result = {}
        
        for header_name in relevant_headers:
            value = headers.get(header_name)
            if value:
                # Ocultar tokens/secrets en Authorization
                if header_name == 'Authorization' and len(value) > 20:
                    result[header_name] = value[:10] + '...[HIDDEN]'
                else:
                    result[header_name] = value
                    
        return result

    def _validate_payload(self, endpoint_name, payload):
        """
        Valida payload según el endpoint con validaciones suaves.
        
        Args:
            endpoint_name (str): 'status' o 'load'
            payload (dict): Datos JSON parseados
            
        Returns:
            list: Lista de warnings (códigos de error suaves)
        """
        warnings = []
        
        if endpoint_name == 'status':
            warnings.extend(self._validate_status_payload(payload))
        elif endpoint_name == 'load':
            warnings.extend(self._validate_load_payload(payload))
            
        return warnings

    def _validate_status_payload(self, payload):
        """
        Valida payload del webhook status.
        
        Expected:
        {
            "reference": "string",
            "status": "SUCCESS" | "ERROR",
            "description": "string" (optional)
        }
        
        Args:
            payload (dict): Payload JSON
            
        Returns:
            list: Lista de warning codes
        """
        warnings = []
        
        # Validar reference
        reference = payload.get('reference')
        if not reference or not isinstance(reference, str) or len(reference) > 30:
            warnings.append('invalid_reference')
        
        # Validar status
        status = payload.get('status')
        if not status or status not in ['SUCCESS', 'ERROR']:
            warnings.append('invalid_status')
        
        # Validar description (opcional)
        description = payload.get('description')
        if description is not None and not isinstance(description, str):
            warnings.append('invalid_description')
            
        return warnings

    def _validate_load_payload(self, payload):
        """
        Valida payload del webhook load.
        
        Expected:
        {
            "machine": "string",
            "slot": "string", 
            "quantity": number
        }
        
        Args:
            payload (dict): Payload JSON
            
        Returns:
            list: Lista de warning codes
        """
        warnings = []
        
        # Validar machine
        machine = payload.get('machine')
        if not machine or not isinstance(machine, str):
            warnings.append('invalid_machine')
        
        # Validar slot
        slot = payload.get('slot')
        if not slot or not isinstance(slot, str):
            warnings.append('invalid_slot')
        
        # Validar quantity
        quantity = payload.get('quantity')
        if quantity is None or not isinstance(quantity, (int, float)):
            warnings.append('invalid_quantity')
            
        return warnings

    def _create_log(self, log_data):
        """
        Crea un registro de log en la base de datos.
        
        Args:
            log_data (dict): Datos para el log
        """
        try:
            request.env['vending.webhook.log'].sudo().create(log_data)
        except Exception:
            # Si falla el logging, no queremos que afecte la respuesta del webhook
            # En un entorno real aquí se podría usar un logger alternativo
            pass