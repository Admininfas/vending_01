# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Controller para webhooks de vending machines.

Implementa 2 endpoints públicos HTTP (type='http'):
- POST /v1/vending/webhook/status: Estado de transacciones (Winfas)
- POST /v1/vending/webhook/load: Información de carga de stock

Seguridad:
- Sin HMAC ni API key. La seguridad se gestiona por whitelist de IP
  a nivel de reverse proxy / firewall.

Formato Winfas:
- POST JSON con { reference, status: SUCCESS|ERROR, description: str }
- Winfas reintenta si no recibe HTTP 200: a los 5s, 10s, 20s
- Winfas puede reenviar incluso después de recibir 200 → idempotencia

Códigos HTTP:
- 200: Procesado correctamente o duplicado
- 400: Error en el payload (culpa de Winfas)
- 404: Recurso no encontrado (machine/slot/order)
- 500: Error interno de Odoo (Winfas debe reintentar)
"""

import json
import logging
from odoo import http, fields  # type: ignore
from odoo.http import request  # type: ignore

_logger = logging.getLogger(__name__)
LOG_SEP = "=" * 60


class VendingWebhookController(http.Controller):
    """Controller para webhooks de vending machines (compatible con Winfas)."""

    # ------------------------------------------------------------------
    # Endpoints públicos
    # ------------------------------------------------------------------
    @http.route(
        '/v1/vending/webhook/status',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def webhook_status(self, **kwargs):
        """
        Recibe actualizaciones de estado de transacciones desde Winfas.

        Payload esperado (Winfas):
        {
            "reference": "string",
            "status": "SUCCESS" | "ERROR",
            "description": "string" (opcional)
        }

        Códigos de respuesta:
        - 200: OK (procesado o duplicado)
        - 400: Payload inválido
        - 500: Error interno (reintentar)
        """
        _logger.info(f"{LOG_SEP}")
        _logger.info(f"[WEBHOOK STATUS] POST /v1/vending/webhook/status recibido")
        return self._process_status_webhook(request)


    @http.route(
        '/v1/vending/webhook/load',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def webhook_load(self, **kwargs):
        """
        Recibe notificación de carga de stock en un slot desde Winfas.

        Payload esperado:
        {
            "machine": "2209270151",  // vending.machine.code
            "slot": 4,                // slot.code (número)
            "quantity": 10            // nueva cantidad en el slot
        }

        Códigos de respuesta:
        - 200: OK (stock actualizado)
        - 400: Payload inválido
        - 404: Machine o slot no encontrado
        - 500: Error interno
        """
        _logger.info(f"{LOG_SEP}")
        _logger.info(f"[WEBHOOK LOAD] POST /v1/vending/webhook/load recibido")
        return self._process_load_webhook(request)


    # ------------------------------------------------------------------
    # Procesamiento de webhooks
    # ------------------------------------------------------------------
    def _process_status_webhook(self, request_obj):
        """Procesa webhook de status con códigos HTTP apropiados."""
        # ── Lectura del body ──
        try:
            raw_body = request_obj.httprequest.get_data(as_text=True)
            _logger.info(f"[WEBHOOK STATUS] Body: {raw_body}")
        except Exception as e:
            _logger.exception("[WEBHOOK STATUS] Error leyendo body")
            return self._make_response('Error reading request body', 500)

        # ── Parsing JSON ──
        try:
            data = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, ValueError) as e:
            _logger.error(f"[WEBHOOK STATUS] JSON inválido: {raw_body}")
            self._log_webhook('', raw_body, 'status',
                              error_msg=f'Invalid JSON: {str(e)}',
                              processing_result='validation_error')
            return self._make_response('Invalid JSON', 400)

        # ── Validación de campos ──
        reference = data.get('reference', '')
        status = data.get('status', '')
        description = data.get('description', '')

        _logger.info(f"[WEBHOOK STATUS] reference={reference}, status={status}")

        if not reference or not status:
            _logger.warning(f"[WEBHOOK STATUS] Campos faltantes")
            self._log_webhook(reference, raw_body, 'status',
                              error_msg='Missing required fields',
                              processing_result='validation_error')
            return self._make_response('Missing required fields: reference and status', 400)

        if status not in ('SUCCESS', 'ERROR'):
            _logger.warning(f"[WEBHOOK STATUS] Status inválido: {status}")
            self._log_webhook(reference, raw_body, 'status',
                              error_msg=f'Invalid status: {status}',
                              processing_result='validation_error')
            return self._make_response(f'Invalid status value: {status}. Must be SUCCESS or ERROR', 400)

        # ── Buscar orden ──
        env = request_obj.env
        order = env['pos.order'].sudo().search([
            ('vending_reference', '=', reference),
        ], limit=1)

        if not order:
            _logger.warning(f"[WEBHOOK STATUS] Orden no encontrada: {reference}")
            self._log_webhook(reference, raw_body, 'status',
                              error_msg='Order not found',
                              processing_result='order_not_found')
            return self._make_response('OK', 200)

        _logger.info(f"[WEBHOOK STATUS] Orden encontrada: id={order.id}, status={order.vending_status}")

        # ── Procesamiento ──
        try:
            audit = order.apply_webhook_status(status, description=description)

            processing_result = audit.get('result', 'internal_error')
            is_processed = audit.get('processed', False)
            actions = audit.get('actions', {})

            if is_processed:
                _logger.info(f"[WEBHOOK STATUS] Orden {order.id} actualizada con status={status}")
                self._notify_kiosk(env, reference, order.vending_status, description)
                actions['bus_notified'] = True
            else:
                _logger.info(
                    f"[WEBHOOK STATUS] Orden {order.id} no actualizada "
                    f"(resultado: {processing_result})"
                )

            self._log_webhook(reference, raw_body, 'status',
                              processing_result=processing_result,
                              actions=actions)
            _logger.info(f"{LOG_SEP}")
            return self._make_response('OK', 200)

        except Exception as e:
            _logger.exception(f"[WEBHOOK STATUS] Error interno procesando")
            self._log_webhook(reference, raw_body, 'status',
                              error_msg=f'Internal error: {str(e)}',
                              processing_result='internal_error')
            _logger.info(f"{LOG_SEP}")
            return self._make_response('Internal server error', 500)

    # ------------------------------------------------------------------
    # Procesamiento de load webhook
    # ------------------------------------------------------------------
    def _process_load_webhook(self, request_obj):
        """
        Procesa webhook de carga de stock.

        Acepta un objeto único o un array de objetos:
          { "machine": "...", "slot": 4, "quantity": 10 }
          [ { "machine": "...", "slot": 4, "quantity": 10 }, ... ]

        En modo array responde siempre HTTP 200 con un JSON de resultados
        individuales. En modo objeto único mantiene la semántica original
        (HTTP 400/404/500 según el error).
        """
        # ── Lectura del body ──
        try:
            raw_body = request_obj.httprequest.get_data(as_text=True)
            _logger.info(f"[WEBHOOK LOAD] Body: {raw_body}")
        except Exception as e:
            _logger.exception("[WEBHOOK LOAD] Error leyendo body")
            return self._make_response('Error reading request body', 500)

        # ── Parsing JSON ──
        try:
            data = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, ValueError) as e:
            _logger.error(f"[WEBHOOK LOAD] JSON inválido: {raw_body}")
            self._log_webhook('', raw_body, 'load', error_msg=f'Invalid JSON: {str(e)}')
            return self._make_response('Invalid JSON', 400)

        # ── Normalizar: lista o objeto único ──
        is_batch = isinstance(data, list)
        items = data if is_batch else [data]

        if not items:
            return self._make_response('Empty array', 400)

        env = request_obj.env
        results = []

        for idx, item in enumerate(items):
            label = f"item[{idx}]" if is_batch else "item"
            result = self._process_single_load_item(env, item, raw_body, label)
            results.append(result)

        # ── Respuesta ──
        if is_batch:
            # Siempre HTTP 200; el caller puede revisar el body para ver fallos individuales
            errors = [r for r in results if r.get('status') != 'ok']
            if errors:
                _logger.warning(f"[WEBHOOK LOAD] Batch completado con {len(errors)} error(es) de {len(results)}")
            else:
                _logger.info(f"[WEBHOOK LOAD] Batch completado: {len(results)} item(s) procesados")
            _logger.info(f"{LOG_SEP}")
            response_body = json.dumps({'results': results}, ensure_ascii=False)
            return request.make_response(
                response_body,
                headers=[('Content-Type', 'application/json')],
                status=200
            )
        else:
            # Objeto único: mantiene semántica original de códigos HTTP
            single = results[0]
            _logger.info(f"{LOG_SEP}")
            return self._make_response(single.get('message', 'OK'), single.get('http_status', 200))

    def _process_single_load_item(self, env, item, raw_body, label='item'):
        """
        Procesa un único objeto de carga de stock.

        Returns:
            dict con claves: status ('ok'|'error'), http_status, message,
                             machine, slot, quantity (cuando aplica)
        """
        machine_code = item.get('machine', '')
        slot_number = item.get('slot')
        quantity = item.get('quantity')

        _logger.info(f"[WEBHOOK LOAD] {label}: machine={machine_code}, slot={slot_number}, quantity={quantity}")

        # ── Validación de campos ──
        if not machine_code or slot_number is None or quantity is None:
            msg = 'Missing required fields: machine, slot, quantity'
            _logger.warning(f"[WEBHOOK LOAD] {label}: {msg}")
            self._log_webhook('', raw_body, 'load', error_msg=msg)
            return {'status': 'error', 'http_status': 400, 'message': msg,
                    'machine': machine_code, 'slot': slot_number}

        try:
            slot_number = int(slot_number)
            quantity = float(quantity)
        except (ValueError, TypeError):
            msg = 'slot and quantity must be numeric'
            _logger.error(f"[WEBHOOK LOAD] {label}: {msg}")
            self._log_webhook('', raw_body, 'load', error_msg=msg)
            return {'status': 'error', 'http_status': 400, 'message': msg,
                    'machine': machine_code, 'slot': slot_number}

        try:
            # Buscar máquina
            machine = env['vending.machine'].sudo().search([
                ('code', '=', machine_code)
            ], limit=1)

            if not machine:
                msg = f'Machine {machine_code} not found'
                _logger.warning(f"[WEBHOOK LOAD] {label}: {msg}")
                self._log_webhook('', raw_body, 'load', error_msg=msg)
                return {'status': 'error', 'http_status': 404, 'message': msg,
                        'machine': machine_code, 'slot': slot_number}

            # Buscar slot
            slot = env['vending.slot'].sudo().search([
                ('machine_id', '=', machine.id),
                ('code', '=', slot_number)
            ], limit=1)

            if not slot:
                msg = f'Slot {slot_number} not found in machine {machine_code}'
                _logger.warning(f"[WEBHOOK LOAD] {label}: {msg}")
                self._log_webhook('', raw_body, 'load', error_msg=msg)
                return {'status': 'error', 'http_status': 404, 'message': msg,
                        'machine': machine_code, 'slot': slot_number}

            # Verificar que el slot tenga producto asignado
            if not slot.product_tmpl_id:
                msg = f'Slot {slot.name} has no product assigned'
                _logger.error(f"[WEBHOOK LOAD] {label}: {msg}")
                self._log_webhook('', raw_body, 'load', error_msg=msg)
                return {'status': 'error', 'http_status': 400, 'message': msg,
                        'machine': machine_code, 'slot': slot_number}

            # Obtener la variante del producto
            product = slot.product_tmpl_id.product_variant_id

            # Buscar/crear stock.quant
            quant = env['stock.quant'].sudo().search([
                ('location_id', '=', slot.location_id.id),
                ('product_id', '=', product.id),
            ], limit=1)

            old_quantity = quant.quantity if quant else 0.0

            if quant:
                quant.quantity = quantity
                _logger.info(
                    f"[WEBHOOK LOAD] {label}: Stock actualizado: "
                    f"{slot.name} ({product.name}): {old_quantity} → {quantity}"
                )
            else:
                quant = env['stock.quant'].sudo().create({
                    'location_id': slot.location_id.id,
                    'product_id': product.id,
                    'quantity': quantity,
                })
                _logger.info(
                    f"[WEBHOOK LOAD] {label}: Stock creado: "
                    f"{slot.name} ({product.name}): {quantity}"
                )

            # Log exitoso
            self._log_webhook(f'{machine_code}/{slot_number}', raw_body, 'load')

            return {
                'status': 'ok',
                'http_status': 200,
                'message': 'OK',
                'machine': machine_code,
                'slot': slot_number,
                'quantity': quantity,
                'previous_quantity': old_quantity,
            }

        except Exception as e:
            msg = f'Internal error: {str(e)}'
            _logger.exception(f"[WEBHOOK LOAD] {label}: Error interno")
            self._log_webhook('', raw_body, 'load', error_msg=msg)
            return {'status': 'error', 'http_status': 500, 'message': msg,
                    'machine': machine_code, 'slot': slot_number}


    # ------------------------------------------------------------------
    # Bus notifications
    # ------------------------------------------------------------------
    def _notify_kiosk(self, env, reference, status, description):
        """Envía notificación instantánea al kiosk via bus.bus."""
        try:
            channel = f'vending_order_{reference}'
            message = {
                'type': 'vending_payment_result',
                'channel': channel,
                'reference': reference,
                'status': status,
                'description': description,
            }
            self.env['bus.bus'].sudo()._sendone(channel, 'notification', message)
            _logger.info(f"[WEBHOOK] Bus notification enviada: channel={channel}, status={status}")
        except Exception:
            _logger.warning(f"[WEBHOOK] Bus no disponible, polling fallback activo")

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @staticmethod
    def _make_response(message, status_code):
        """Crea response de texto plano con el código HTTP apropiado."""
        return request.make_response(
            message,
            headers=[('Content-Type', 'text/plain')],
            status=status_code
        )

    def _log_webhook(self, reference, raw_body, endpoint_type, *,
                     error_msg=None, processing_result=None, actions=None):
        """
        Registra el webhook en el log de auditoría.

        Args:
            reference: Referencia de la transacción
            raw_body: Body crudo del request
            endpoint_type: 'status' o 'load'
            error_msg: Mensaje de error (si hubo)
            processing_result: 'processed', 'duplicate', 'late_arrival', etc.
            actions: Dict con acciones realizadas (se serializa a JSON compacto)
        """
        try:
            vals = {
                'endpoint': endpoint_type,
                'payload_json': raw_body or '',
                'reference': reference or '',
                'error_message': error_msg or '',
            }

            if processing_result:
                vals['processing_result'] = processing_result

            if actions:
                vals['actions_json'] = json.dumps(actions, ensure_ascii=False, default=str)

            request.env['vending.webhook.log'].sudo().create(vals)
        except Exception:
            _logger.exception(f"[WEBHOOK] Error creando log de auditoría")
