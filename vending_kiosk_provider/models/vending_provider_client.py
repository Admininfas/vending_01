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

    def _get_machine_by_identifier(self, machine_identifier):
        machine = self.env['vending.machine'].sudo().search([
            ('code', '=', machine_identifier),
        ], limit=1)
        if not machine:
            raise UserError(f'No existe máquina con identificador {machine_identifier}')
        return machine

    def _get_machine_by_reference(self, reference):
        order = self.env['pos.order'].sudo().search([
            ('vending_reference', '=', reference),
        ], limit=1)
        return order.vending_machine_id if order else False

    def _build_headers(self, machine):
        if not machine:
            raise UserError('No se pudo determinar la máquina para enviar API key al proveedor')

        api_key = machine.get_api_key()
        if not api_key:
            raise UserError(
                f'La máquina {machine.name} no tiene API key configurada para proveedor'
            )

        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'x-api-key': api_key,
        }

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
        
        machine = self._get_machine_by_identifier(machine_identifier)
        headers = self._build_headers(machine)

        # Usar proveedor externo real o dummy HTTP
        base_url = self._get_base_url()
        if '/dummy' in base_url:
            _logger.info('[PROVIDER CLIENT] Dummy detectado por base_url. Usando fallback interno sin HTTP externo.')
            return self._request_qr_dummy(
                machine_identifier,
                reference,
                amount_cents,
                slot_number,
                description,
                timeout,
            )

        qr_endpoints = [
            f"{base_url}/payment/qr/{machine_identifier}",
            f"{base_url}/payments/qr/{machine_identifier}",
        ]
        
        payload = {
            'reference': reference,
            'amount': amount_cents,
            'slot': slot_number,
            'description': description or 'Ventas Infas',
            'timeout': timeout,
        }
        
        _logger.info(
            "[PROVIDER CLIENT] Endpoint externo (prioridad): POST %s",
            qr_endpoints[0],
        )
        _logger.info(f"[PROVIDER CLIENT] Payload: {json.dumps(payload)}")
        
        try:
            last_error_msg = None
            for index, endpoint in enumerate(qr_endpoints):
                _logger.info("[PROVIDER CLIENT] Intento %s -> POST %s", index + 1, endpoint)
                response = requests.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=HTTP_REQUEST_TIMEOUT,
                )

                _logger.info(f"[PROVIDER CLIENT] Response status: {response.status_code}")
                _logger.info(
                    "[PROVIDER CLIENT] Response content-type: %s",
                    response.headers.get('Content-Type', ''),
                )

                if response.status_code != 200:
                    error_msg = self._parse_error_response(response)
                    last_error_msg = f"Error del proveedor: {error_msg}"
                    _logger.error(
                        "[PROVIDER CLIENT] ✗ Error en endpoint %s: %s",
                        endpoint,
                        error_msg,
                    )
                    if index == 0 and response.status_code in (404, 405):
                        _logger.warning(
                            "[PROVIDER CLIENT] Reintentando con endpoint alternativo por status %s",
                            response.status_code,
                        )
                        continue
                    raise UserError(last_error_msg)

                raw_body = response.text or ''
                if not raw_body.strip():
                    last_error_msg = "El proveedor devolvió una respuesta vacía al generar QR"
                    _logger.error("[PROVIDER CLIENT] ✗ Respuesta 200 vacía del proveedor")
                    if index == 0:
                        _logger.warning("[PROVIDER CLIENT] Reintentando con endpoint alternativo por body vacío")
                        continue
                    raise UserError(last_error_msg)

                try:
                    data = response.json()
                except ValueError:
                    body_preview = raw_body[:300].replace('\n', ' ').replace('\r', ' ')
                    last_error_msg = "El proveedor devolvió una respuesta no válida al generar QR"
                    _logger.error(
                        "[PROVIDER CLIENT] ✗ Respuesta 200 no-JSON. Body preview: %s",
                        body_preview,
                    )
                    if index == 0:
                        _logger.warning("[PROVIDER CLIENT] Reintentando con endpoint alternativo por JSON inválido")
                        continue
                    raise UserError(last_error_msg)

                _logger.info(f"[PROVIDER CLIENT] ✓ Response body: {json.dumps(data)[:200]}...")

                # Acepta formato real (data_url) y dummy (url)
                qr_url = data.get('data_url') or data.get('url')
                if not qr_url or 'content' not in data:
                    _logger.error(f"[PROVIDER CLIENT] ✗ Respuesta inválida: {data}")
                    if index == 0:
                        _logger.warning("[PROVIDER CLIENT] Reintentando con endpoint alternativo por schema inválido")
                        continue
                    raise UserError("Respuesta inválida del proveedor")

                _logger.info(f"[PROVIDER CLIENT] ✓ QR generado exitosamente")
                _logger.info(f"[PROVIDER CLIENT] === FIN SOLICITUD QR ===")
                _logger.info(f"{LOG_SEP}")

                return {
                    'url': qr_url,
                    'content': data['content'],
                    'timeout': timeout,
                }

            raise UserError(last_error_msg or "No se pudo generar QR con los endpoints configurados")
            
        except requests.exceptions.Timeout:
            _logger.error(f"[PROVIDER CLIENT] ✗ Timeout contactando proveedor")
            raise UserError("Tiempo de espera agotado al contactar al proveedor")
            
        except requests.exceptions.ConnectionError as e:
            _logger.error(f"[PROVIDER CLIENT] ✗ Error de conexión: {e}")
            raise UserError("Error de conexión con el proveedor")
            
        except requests.exceptions.RequestException as e:
            _logger.error(f"[PROVIDER CLIENT] ✗ Request error: {e}")
            raise UserError(f"Error al comunicarse con el proveedor: {str(e)}")

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
        if '/dummy' in base_url:
            return self._check_status_dummy(reference)

        machine = self._get_machine_by_reference(reference)
        headers = self._build_headers(machine)
        
        try:
            response = requests.post(
                endpoint,
                headers=headers,
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
        Fallback local para dummy cuando el endpoint HTTP externo no es accesible
        desde el contenedor (ej: localhost:8087).
        """
        qr_uuid = str(uuid.uuid4())
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url').rstrip('/')
        qr_url = f"{base_url}/dummy/payments/qr/image/{qr_uuid}"
        qr_content = f"DUMMY_QR:{machine_identifier}:{reference}:{amount_cents}:{slot_number}"

        _logger.info('[PROVIDER CLIENT] ✓ QR dummy fallback generado: %s', qr_url)
        _logger.info(f"[PROVIDER CLIENT] === FIN SOLICITUD QR ===")
        _logger.info(f"{LOG_SEP}")

        return {
            'url': qr_url,
            'content': qr_content,
            'timeout': timeout,
        }

    def _check_status_dummy(self, reference):
        """Fallback dummy para consultas de estado cuando no hay HTTP externo."""
        return {
            'reference': reference,
            'status': 'PENDING',
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
