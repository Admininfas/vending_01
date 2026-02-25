# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Cliente para comunicación con el proveedor de vending (Winfas).

Este modelo abstrae la comunicación con la API externa de Winfas.
La URL base se configura desde Ajustes > Parámetros del Sistema:
- Clave: vending.provider_base_url
- Valor: URL del proveedor (ej: https://api-v2.winfas.com.ar)
- Si no está configurada, usa el endpoint dummy local para testing.
"""

import json
import uuid
import logging
import requests
from odoo import api, models  # type: ignore
from odoo.exceptions import UserError  # type: ignore

_logger = logging.getLogger(__name__)
LOG_SEP = "=" * 60

# Timeout por defecto para QRs (en segundos)
DEFAULT_QR_TIMEOUT = 60

# Timeout para requests HTTP (en segundos)
HTTP_REQUEST_TIMEOUT = 30


class VendingProviderClient(models.AbstractModel):
    """
    Cliente para comunicación con la API del proveedor de vending.
    
    Métodos principales:
    - request_qr(): Solicita generación de QR de pago
    - check_status(): Consulta estado de una referencia
    """
    
    _name = 'vending.provider.client'
    _description = 'Vending Provider Client'

    def _get_base_url(self):
        """
        Obtiene la URL base del proveedor desde parámetros del sistema.
        
        Configurable en: Ajustes > Técnicos > Parámetros del Sistema
        Clave: vending.provider_base_url
        
        Returns:
            str: URL base (proveedor real o dummy local)
        """
        provider_url = self.env['ir.config_parameter'].sudo().get_param(
            'vending.provider_base_url', default=''
        )
        if provider_url:
            return provider_url.rstrip('/')
        
        # Sin URL configurada -> usar endpoint dummy local
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}/dummy"

    def _is_dummy_mode(self):
        """Retorna True si se usa el dummy provider (sin URL externa configurada)."""
        provider_url = self.env['ir.config_parameter'].sudo().get_param(
            'vending.provider_base_url', default=''
        )
        return not bool(provider_url)

    def request_qr(self, machine_identifier, reference, amount_cents, slot_number, description=None, timeout=None):
        """
        Solicita la generación de un QR de pago al proveedor.
        
        Args:
            machine_identifier (str): Identificador único de la máquina vending
            reference (str): Referencia única de la transacción (max 36 chars)
            amount_cents (int): Monto en centavos (ej: 10000 = $100.00)
            slot_number (int): Número del slot que despachará el producto
            description (str, optional): Descripción del producto
            timeout (int, optional): Tiempo de vida del QR en segundos
            
        Returns:
            dict: {
                'url': str,      # URL de la imagen del QR
                'content': str,  # Contenido del QR (para regenerar)
                'timeout': int,  # Tiempo de vida en segundos
            }
            
        Raises:
            UserError: Si hay error en la comunicación o validación
        """
        if timeout is None:
            timeout = DEFAULT_QR_TIMEOUT
        
        # Validar inputs
        self._validate_qr_request(reference, amount_cents, slot_number, timeout)
        
        _logger.info(f"{LOG_SEP}")
        _logger.info(f"[PROVIDER CLIENT] === SOLICITANDO QR AL PROVEEDOR ===")
        
        # Si no hay URL externa configurada, usar dummy interno
        if self._is_dummy_mode():
            _logger.info(f"[PROVIDER CLIENT] Usando dummy provider interno")
            return self._request_qr_dummy(machine_identifier, reference, amount_cents, slot_number, description, timeout)
        
        # Usar proveedor externo real
        base_url = self._get_base_url()
        endpoint = f"{base_url}/payment/qr/{machine_identifier}"
        
        payload = {
            'reference': reference,
            'amount': amount_cents,
            'slot': slot_number,
            'description': description or 'Ventas Infas',
            'timeout': timeout,
        }
        
        _logger.info(f"[PROVIDER CLIENT] Endpoint externo: POST {endpoint}")
        _logger.info(f"[PROVIDER CLIENT] Payload: {json.dumps(payload)}")
        
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=HTTP_REQUEST_TIMEOUT,
            )
            
            _logger.info(f"[PROVIDER CLIENT] Response status: {response.status_code}")
            
            if response.status_code != 200:
                error_msg = self._parse_error_response(response)
                _logger.error(f"[PROVIDER CLIENT] ✗ Error del proveedor: {error_msg}")
                raise UserError(f"Error del proveedor: {error_msg}")
            
            data = response.json()
            _logger.info(f"[PROVIDER CLIENT] ✓ Response body: {json.dumps(data)[:200]}...")
            
            # Validar respuesta (Winfas devuelve 'content' y 'data_url')
            if 'data_url' not in data or 'content' not in data:
                _logger.error(f"[PROVIDER CLIENT] ✗ Respuesta inválida: {data}")
                raise UserError("Respuesta inválida del proveedor")
            
            _logger.info(f"[PROVIDER CLIENT] ✓ QR generado exitosamente")
            _logger.info(f"[PROVIDER CLIENT] === FIN SOLICITUD QR ===")
            _logger.info(f"{LOG_SEP}")
            
            return {
                'url': data['data_url'],
                'content': data['content'],
                'timeout': timeout,
            }
            
        except requests.exceptions.Timeout:
            _logger.error(f"[PROVIDER CLIENT] ✗ Timeout contactando proveedor")
            raise UserError("Tiempo de espera agotado al contactar al proveedor")
            
        except requests.exceptions.ConnectionError as e:
            _logger.error(f"[PROVIDER CLIENT] ✗ Error de conexión: {e}")
            raise UserError("Error de conexión con el proveedor")
            
        except requests.exceptions.RequestException as e:
            _logger.error(f"[PROVIDER CLIENT] ✗ Request error: {e}")
            raise UserError(f"Error al comunicarse con el proveedor: {str(e)}")
            
        except json.JSONDecodeError:
            _logger.error("[PROVIDER CLIENT] ✗ JSON inválido del proveedor")
            raise UserError("Respuesta inválida del proveedor")

    def check_status(self, reference):
        """
        Consulta el estado de una referencia en el proveedor.
        
        Args:
            reference (str): Referencia de la transacción
            
        Returns:
            dict: {
                'reference': str,
                'status': str,  # 'SUCCESS', 'PENDING', 'ERROR', 'EXPIRED', 'NOT_FOUND'
            }
        """
        base_url = self._get_base_url()
        endpoint = f"{base_url}/status/{reference}"
        
        try:
            response = requests.post(
                endpoint,
                headers={'Content-Type': 'application/json'},
                timeout=HTTP_REQUEST_TIMEOUT,
            )
            
            data = response.json()
            return {
                'reference': data.get('reference', reference),
                'status': data.get('status', 'UNKNOWN'),
            }
            
        except Exception as e:
            _logger.warning(f"Error checking status for {reference}: {e}")
            return {
                'reference': reference,
                'status': 'ERROR',
            }

    def _validate_qr_request(self, reference, amount_cents, slot_number, timeout):
        """
        Valida los parámetros del request de QR.
        
        Raises:
            UserError: Si algún parámetro es inválido
        """
        if not reference or not isinstance(reference, str):
            raise UserError("La referencia es requerida")
        
        if len(reference) > 36:
            raise UserError("La referencia no puede tener más de 36 caracteres")
        
        if not isinstance(amount_cents, int) or amount_cents <= 0:
            raise UserError("El monto debe ser un número entero positivo")
        
        if not isinstance(slot_number, int) or slot_number <= 0:
            raise UserError("El número de slot debe ser un entero positivo")
        
        if not isinstance(timeout, int) or timeout <= 0:
            raise UserError("El timeout debe ser un número positivo")

    def _request_qr_dummy(self, machine_identifier, reference, amount_cents, slot_number, description, timeout):
        """
        Solicita QR usando el dummy provider interno (sin HTTP).
        """
        # Generar UUID único para el QR
        qr_uuid = str(uuid.uuid4())
        
        # Generar URLs dummy
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        qr_url = f"{base_url}/dummy/qr/{qr_uuid}"
        qr_content = f"DUMMY_QR:{reference}:{amount_cents}"
        
        _logger.info(f"[PROVIDER CLIENT] ✓ QR dummy generado: url={qr_url}")
        _logger.info(f"[PROVIDER CLIENT] === FIN SOLICITUD QR ===")
        _logger.info(f"{LOG_SEP}")
        
        return {
            'url': qr_url,
            'content': qr_content,
            'timeout': timeout,
        }

    def _parse_error_response(self, response):
        """
        Intenta extraer mensaje de error de la respuesta.
        
        Args:
            response: Response object de requests
            
        Returns:
            str: Mensaje de error
        """
        try:
            data = response.json()
            return data.get('error', f"HTTP {response.status_code}")
        except Exception:
            return f"HTTP {response.status_code}: {response.text[:100]}"
