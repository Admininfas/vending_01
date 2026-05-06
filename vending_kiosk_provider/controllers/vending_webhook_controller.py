# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
Controller para webhooks de vending machines.

Implementa 2 endpoints públicos HTTP (type='http'):
- POST /v1/vending/webhook/payment_status: Estado de pago
- POST /v1/vending/webhook/delivery_status: Estado de entrega
- POST /v1/vending/webhook/load: Información de carga de stock
- POST /v1/vending/webhook/alarm: Bloqueo/desbloqueo por falla

Seguridad:
- Requiere API key por máquina en header x-api-key.

Formato Winfas:
- payment_status: { reference, status, description }
- delivery_status: { reference, status: SUCCESS|ERROR, description }
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
from datetime import timedelta

from odoo import http, fields  # type: ignore
from odoo.http import request  # type: ignore

_logger = logging.getLogger(__name__)
LOG_SEP = "=" * 60

# Estados de pos.order que indican que el dispatch al hardware fue disparado
# recientemente: solo estas órdenes son candidatas plausibles para asociar
# una alarma "error 80" sin slot explícito del proveedor.
# Incluye payment_error porque aunque el pago falló, la máquina sigue intentando
# despachar en ese slot y puede generar alarmas relacionadas.
_ALARM_DEDUCIBLE_VENDING_STATES = ('payment_success', 'payment_error', 'vending_delivery_error')
# Ventana temporal para correlacionar alarma → orden. Más allá de esto, las
# retransmisiones del hardware no deben pegarle a una orden posterior no
# relacionada (que es exactamente lo que bloqueaba slots sanos en producción).
_ALARM_DEDUCTION_WINDOW_SECONDS = 120


class VendingWebhookController(http.Controller):
    """Controller para webhooks de vending machines (compatible con Winfas)."""
    # 'APPROVED': 'Pago Aprobado', 
    # 'AUTHORIZED': 'Pago Autorizado',
    # 'IN_PROCESS': 'Pago en Revisión', 
    # 'REJECTED': 'Pago Rechazado', 
    # 'CANCELLED': 'Pago Cancelado', 
    # 'REFUNDED': 'Pago Devuelto'


    PAYMENT_STATUS_VALUES = {
        'APPROVED',
        'AUTHORIZED',
        'IN_PROCESS',
        'REJECTED',
        'CANCELLED',
        'REFUNDED',
    }
    DELIVERY_STATUS_VALUES = {'SUCCESS', 'ERROR'}
    ALARM_STATUS_VALUES = {'FAIL', 'SUCCESS'}
    ALARM_SCOPE_VALUES = {'MACHINE', 'SLOT'}

    # ------------------------------------------------------------------
    # Endpoints públicos
    # ------------------------------------------------------------------
    @http.route(
        '/v1/vending/webhook/payment_status',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def webhook_payment_status(self, **kwargs):
        _logger.info(f"{LOG_SEP}")
        _logger.info('[WEBHOOK PAYMENT] POST /v1/vending/webhook/payment_status recibido')
        return self._process_payment_status_webhook(request)

    @http.route(
        '/v1/vending/webhook/delivery_status',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def webhook_delivery_status(self, **kwargs):
        _logger.info(f"{LOG_SEP}")
        _logger.info('[WEBHOOK DELIVERY] POST /v1/vending/webhook/delivery_status recibido')
        return self._process_delivery_status_webhook(request)

    @http.route(
        '/v1/vending/webhook/alarm',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def webhook_alarm(self, **kwargs):
        _logger.info(f"{LOG_SEP}")
        _logger.info('[WEBHOOK ALARM] POST /v1/vending/webhook/alarm recibido')
        return self._process_alarm_webhook(request)


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
    @staticmethod
    def _make_json_response(payload, status_code=200):
        body = json.dumps(payload, ensure_ascii=False, default=str)
        return request.make_response(
            body,
            headers=[('Content-Type', 'application/json')],
            status=status_code,
        )

    def _extract_api_key(self, request_obj):
        """Obtiene x-api-key contemplando case-insensitive en header name."""
        headers = request_obj.httprequest.headers
        for key, value in headers.items():
            if key and key.lower() == 'x-api-key':
                return (value or '').strip()
        return ''

    def _authenticate_machine(self, machine, request_obj, reference, raw_body, endpoint_type):
        """Valida API key contra la máquina objetivo y devuelve response en error."""
        inbound_key = self._extract_api_key(request_obj)
        if not machine.api_key_configured:
            self._log_webhook(
                reference,
                raw_body,
                endpoint_type,
                error_msg='Machine has no API key configured',
                processing_result='auth_error',
            )
            return self._make_response('Unauthorized', 401)

        if not machine.is_api_key_valid(inbound_key):
            self._log_webhook(
                reference,
                raw_body,
                endpoint_type,
                error_msg='Invalid API key',
                processing_result='auth_error',
            )
            return self._make_response('Unauthorized', 401)
        return None

    def _deduce_slot_from_last_transaction(self, machine, env):
        """
        Deduce el slot afectado por una alarma sin array ``slots`` explícito.

        Contexto: el hardware emite "error 80" sin informar a qué slot
        corresponde y, una vez que entra en estado de falla, retransmite la
        alarma en loop. Una correlación ingenua "última orden de la máquina"
        atribuye incorrectamente esas retransmisiones a órdenes nuevas no
        relacionadas, bloqueando slots sanos en cadena.

        Para evitarlo solo se consideran candidatas las órdenes que:
          - pertenecen a la máquina,
          - están en estado ``payment_success`` (dispatch en curso) o
            ``vending_delivery_error`` (dispatch que acaba de fallar),
          - fueron creadas dentro de una ventana reciente
            (``_ALARM_DEDUCTION_WINDOW_SECONDS``).

        Si no hay candidato plausible se devuelve ``None`` y el caller
        registra la alarma como "sin correlación" en lugar de bloquear un
        slot arbitrario.

        Returns:
            int: ``slot_code`` de la orden correlacionada, o ``None``.
        """
        try:
            cutoff = fields.Datetime.now() - timedelta(
                seconds=_ALARM_DEDUCTION_WINDOW_SECONDS
            )
            candidate = env['pos.order'].sudo().search([
                ('vending_machine_id', '=', machine.id),
                ('vending_slot_id', '!=', False),
                ('vending_status', 'in', list(_ALARM_DEDUCIBLE_VENDING_STATES)),
                ('create_date', '>=', fields.Datetime.to_string(cutoff)),
            ], order='create_date desc', limit=1)

            if candidate and candidate.vending_slot_id:
                slot_code = candidate.vending_slot_id.code
                _logger.info(
                    "[WEBHOOK ALARM] Slot deducido: slot_code=%s, order_id=%s, "
                    "vending_status=%s, create_date=%s",
                    slot_code,
                    candidate.id,
                    candidate.vending_status,
                    candidate.create_date,
                )
                return slot_code

            _logger.warning(
                "[WEBHOOK ALARM] Sin orden candidata para correlacionar alarma en "
                "máquina %s (ventana %ss, estados %s). Se omite la alarma.",
                machine.code,
                _ALARM_DEDUCTION_WINDOW_SECONDS,
                list(_ALARM_DEDUCIBLE_VENDING_STATES),
            )
            return None
        except Exception as e:
            _logger.warning(
                "[WEBHOOK ALARM] Error deduciendo slot de última transacción: %s",
                e,
            )
            return None

    def _parse_request_json(self, request_obj, endpoint_type):
        """Lee body + parsea JSON. Retorna (raw_body, data, response_error)."""
        # ── Lectura del body ──
        try:
            raw_body = request_obj.httprequest.get_data(as_text=True)
            _logger.info('[WEBHOOK %s] Body: %s', endpoint_type.upper(), raw_body)
        except Exception:
            _logger.exception('[WEBHOOK %s] Error leyendo body', endpoint_type.upper())
            return '', None, self._make_response('Error reading request body', 500)

        # ── Parsing JSON ──
        try:
            data = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, ValueError) as error:
            _logger.error('[WEBHOOK %s] JSON inválido: %s', endpoint_type.upper(), raw_body)
            self._log_webhook(
                '', raw_body, endpoint_type,
                error_msg=f'Invalid JSON: {str(error)}',
                processing_result='validation_error',
            )
            return raw_body, None, self._make_response('Invalid JSON', 400)

        return raw_body, data, None

    def _process_payment_status_webhook(self, request_obj):
        """Procesa webhook de pago con validación de API key."""
        raw_body, data, response_error = self._parse_request_json(request_obj, 'payment_status')
        if response_error:
            return response_error

        # ── Validación de campos ──
        reference = data.get('reference', '')
        status = str(data.get('status', '')).upper()
        description = data.get('description', '')

        _logger.info('[WEBHOOK PAYMENT] reference=%s, status=%s', reference, status)

        if not reference or not status:
            _logger.warning('[WEBHOOK PAYMENT] Campos faltantes')
            self._log_webhook(reference, raw_body, 'payment_status',
                              error_msg='Missing required fields',
                              processing_result='validation_error')
            return self._make_response('Missing required fields: reference and status', 400)

        if status not in self.PAYMENT_STATUS_VALUES:
            _logger.warning('[WEBHOOK PAYMENT] Status inválido: %s', status)
            self._log_webhook(reference, raw_body, 'payment_status',
                              error_msg=f'Invalid status: {status}',
                              processing_result='validation_error')
            return self._make_response('Invalid payment status value', 400)

        # ── Buscar orden ──
        env = request_obj.env
        order = env['pos.order'].sudo().search([
            ('vending_reference', '=', reference),
        ], limit=1)

        if not order:
            _logger.warning('[WEBHOOK PAYMENT] Orden no encontrada: %s', reference)
            self._log_webhook(reference, raw_body, 'payment_status',
                              error_msg='Order not found',
                              processing_result='order_not_found')
            return self._make_response('OK', 200)

        auth_error = self._authenticate_machine(order.vending_machine_id, request_obj, reference, raw_body, 'payment_status')
        if auth_error:
            return auth_error

        _logger.info('[WEBHOOK PAYMENT] Orden encontrada: id=%s, status=%s', order.id, order.vending_status)

        # ── Procesamiento ──
        try:
            actions = {'payment_status': status}
            is_processed = False
            processing_result = 'processed'
            notification_description = description

            if status in ('APPROVED', 'AUTHORIZED'):
                is_processed = order.mark_as_payment_success()
                actions['marked_as'] = 'payment_success'
            elif status in ('REJECTED', 'CANCELLED', 'REFUNDED'):
                payment_error_code = description or f'PAYMENT_{status}'
                payment_error_description = order._get_user_friendly_error_description(payment_error_code)
                is_processed = order.mark_as_payment_error(error_description=payment_error_description)
                actions['marked_as'] = 'payment_error'
                actions['payment_error_code'] = payment_error_code
                actions['payment_error_description'] = payment_error_description
                notification_description = payment_error_description
            else:
                # IN_PROCESS: evento válido, sin cambio terminal
                actions['marked_as'] = 'no_change'

            if is_processed:
                _logger.info('[WEBHOOK PAYMENT] Orden %s actualizada con status=%s', order.id, status)
                self._notify_kiosk(env, reference, order.vending_status, notification_description)
                actions['bus_notified'] = True
            else:
                _logger.info(
                    '[WEBHOOK PAYMENT] Orden %s sin cambio de estado terminal', order.id
                )

            self._log_webhook(reference, raw_body, 'payment_status',
                              processing_result=processing_result,
                              actions=actions)
            _logger.info(f"{LOG_SEP}")
            return self._make_response('OK', 200)

        except Exception as error:
            _logger.exception('[WEBHOOK PAYMENT] Error interno procesando')
            self._log_webhook(reference, raw_body, 'payment_status',
                              error_msg=f'Internal error: {str(error)}',
                              processing_result='internal_error')
            _logger.info(f"{LOG_SEP}")
            return self._make_response('Internal server error', 500)

    def _process_delivery_status_webhook(self, request_obj):
        """Procesa webhook de entrega con validación de API key."""
        raw_body, data, response_error = self._parse_request_json(request_obj, 'delivery_status')
        if response_error:
            return response_error

        reference = data.get('reference', '')
        status = str(data.get('status', '')).upper()
        description = data.get('description', '')

        _logger.info('[WEBHOOK DELIVERY] reference=%s, status=%s', reference, status)

        if not reference or not status:
            self._log_webhook(reference, raw_body, 'delivery_status',
                              error_msg='Missing required fields',
                              processing_result='validation_error')
            return self._make_response('Missing required fields: reference and status', 400)

        if status not in self.DELIVERY_STATUS_VALUES:
            self._log_webhook(reference, raw_body, 'delivery_status',
                              error_msg=f'Invalid status: {status}',
                              processing_result='validation_error')
            return self._make_response('Invalid delivery status value', 400)

        env = request_obj.env
        order = env['pos.order'].sudo().search([
            ('vending_reference', '=', reference),
        ], limit=1)

        if not order:
            self._log_webhook(reference, raw_body, 'delivery_status',
                              error_msg='Order not found',
                              processing_result='order_not_found')
            return self._make_response('OK', 200)

        auth_error = self._authenticate_machine(order.vending_machine_id, request_obj, reference, raw_body, 'delivery_status')
        if auth_error:
            return auth_error

        provider_status = 'SUCCESS' if status == 'SUCCESS' else 'ERROR'

        try:
            audit = order.apply_webhook_status(provider_status, description=description)
            processing_result = audit.get('result', 'internal_error')
            is_processed = audit.get('processed', False)
            actions = audit.get('actions', {})

            if is_processed:
                self._notify_kiosk(env, reference, order.vending_status, description)
                actions['bus_notified'] = True

            self._log_webhook(reference, raw_body, 'delivery_status',
                              processing_result=processing_result,
                              actions=actions)
            _logger.info(f"{LOG_SEP}")
            return self._make_response('OK', 200)

        except Exception as error:
            _logger.exception('[WEBHOOK DELIVERY] Error interno procesando')
            self._log_webhook(reference, raw_body, 'delivery_status',
                              error_msg=f'Internal error: {str(error)}',
                              processing_result='internal_error')
            _logger.info(f"{LOG_SEP}")
            return self._make_response('Internal server error', 500)

    def _process_alarm_webhook(self, request_obj):
        """Procesa webhook de alarmas de máquina/slots con validación de API key."""
        raw_body, data, response_error = self._parse_request_json(request_obj, 'alarm')
        if response_error:
            return response_error

        machine_code = str(data.get('machine', '') or '').strip()
        scope = str(data.get('scope', '') or '').upper().strip()
        status = str(data.get('status', '') or '').upper().strip()
        raw_slots = data.get('slots', [])

        if not machine_code or scope not in self.ALARM_SCOPE_VALUES or status not in self.ALARM_STATUS_VALUES:
            self._log_webhook(
                machine_code,
                raw_body,
                'alarm',
                error_msg='Invalid payload: machine, scope(MACHINE|SLOT), status(FAIL|SUCCESS) are required',
                processing_result='validation_error',
            )
            return self._make_response(
                'Invalid payload: machine, scope(MACHINE|SLOT), status(FAIL|SUCCESS) are required',
                400,
            )

        env = request_obj.env
        machine = env['vending.machine'].sudo().search([('code', '=', machine_code)], limit=1)
        if not machine:
            self._log_webhook(
                machine_code,
                raw_body,
                'alarm',
                error_msg=f'Machine {machine_code} not found',
                processing_result='validation_error',
            )
            return self._make_response(f'Machine {machine_code} not found', 404)

        auth_error = self._authenticate_machine(machine, request_obj, machine_code, raw_body, 'alarm')
        if auth_error:
            return auth_error

        try:
            actions = {
                'machine': machine_code,
                'scope': scope,
                'status': status,
                'machine_was_fault_blocked': bool(machine.is_fault_blocked),
            }

            should_notify = False
            invalid_slot_values = []
            missing_slots = []
            processed_slot_codes = []
            slots_changed_codes = []
            slots_unchanged_codes = []
            summary = None

            if scope == 'MACHINE':
                machine_blocked = status == 'FAIL'
                machine_changed = bool(machine.is_fault_blocked) != machine_blocked
                slots_to_update = machine.slot_ids.filtered(
                    lambda s: bool(s.is_fault_blocked) != machine_blocked
                )
                slots_already_ok = machine.slot_ids - slots_to_update

                if machine_changed:
                    machine.sudo().write({'is_fault_blocked': machine_blocked})
                if slots_to_update:
                    slots_to_update.sudo().write({'is_fault_blocked': machine_blocked})

                processed_slot_codes = sorted(machine.slot_ids.mapped('code'))
                slots_changed_codes = sorted(slots_to_update.mapped('code'))
                slots_unchanged_codes = sorted(slots_already_ok.mapped('code'))
                state_changed = bool(machine_changed or slots_to_update)
                # En SUCCESS reemitimos el bus aunque sea idempotente: el
                # firmware reenvía SUCCESS varias veces y queremos que cada
                # reintento le dé otra chance al kiosko de salir de la
                # pantalla de fuera de servicio si la primera notificación
                # se perdió (típico en Odoo SH + WebView Android).
                should_notify = state_changed or not machine_blocked
                processing_result = 'processed' if state_changed else 'no_change'

                if state_changed:
                    verb = 'bloqueada' if machine_blocked else 'reactivada'
                    summary = (
                        f"Máquina {machine_code} {verb}"
                        + (f" ({len(slots_changed_codes)} slots actualizados)"
                           if slots_changed_codes else "")
                    )
                else:
                    summary = (
                        f"Máquina {machine_code} ya estaba "
                        f"{'bloqueada' if machine_blocked else 'activa'} — sin cambios"
                    )
            else:
                deduced_slot_code = None
                if not isinstance(raw_slots, list) or not raw_slots:
                    deduced_slot_code = self._deduce_slot_from_last_transaction(machine, env)

                    if deduced_slot_code is None:
                        summary = (
                            f"Alarma SLOT {status} sin slot en payload y sin orden "
                            f"candidata en la ventana — se omite"
                        )
                        actions.update({
                            'processed_slot_codes': [],
                            'invalid_slot_values': [],
                            'missing_slots': [],
                            'slots_changed_codes': [],
                            'slots_unchanged_codes': [],
                            'machine_is_fault_blocked': bool(machine.is_fault_blocked),
                            'machine_fault_blocked_slots_count': machine.fault_blocked_slots_count,
                            'deduced_slot_code': None,
                            'deduction_used': True,
                            'skip_reason': 'no_candidate_order_in_window',
                            'deduction_window_seconds': _ALARM_DEDUCTION_WINDOW_SECONDS,
                            'deduction_eligible_states': list(_ALARM_DEDUCIBLE_VENDING_STATES),
                        })
                        self._log_webhook(
                            machine_code,
                            raw_body,
                            'alarm',
                            processing_result='skipped_no_candidate',
                            actions=actions,
                            summary=summary,
                        )
                        _logger.info(f"{LOG_SEP}")
                        return self._make_json_response({
                            'status': 'ok',
                            'machine': machine_code,
                            'scope': scope,
                            'result': 'skipped_no_candidate',
                            'machine_is_fault_blocked': bool(machine.is_fault_blocked),
                            'machine_fault_blocked_slots_count': machine.fault_blocked_slots_count,
                            'processed_slot_codes': [],
                            'missing_slots': [],
                            'invalid_slot_values': [],
                        }, 200)

                    raw_slots = [deduced_slot_code]
                    actions['deduced_slot_code'] = deduced_slot_code
                    actions['deduction_used'] = True
                    _logger.info(
                        "[WEBHOOK ALARM] Usando slot deducido: %s", deduced_slot_code
                    )

                requested_slot_codes = []
                seen_codes = set()
                for raw_slot in raw_slots:
                    try:
                        slot_code = int(raw_slot)
                    except (TypeError, ValueError):
                        invalid_slot_values.append(raw_slot)
                        continue

                    if slot_code in seen_codes:
                        continue
                    seen_codes.add(slot_code)
                    requested_slot_codes.append(slot_code)

                valid_slots = env['vending.slot'].sudo().browse()
                if requested_slot_codes:
                    valid_slots = env['vending.slot'].sudo().search([
                        ('machine_id', '=', machine.id),
                        ('code', 'in', requested_slot_codes),
                    ])

                found_codes = set(valid_slots.mapped('code'))
                missing_slots = [code for code in requested_slot_codes if code not in found_codes]
                processed_slot_codes = sorted(found_codes)

                slot_blocked = status == 'FAIL'
                slots_to_update = valid_slots.filtered(
                    lambda s: bool(s.is_fault_blocked) != slot_blocked
                )
                slots_already_ok = valid_slots - slots_to_update
                slots_changed_codes = sorted(slots_to_update.mapped('code'))
                slots_unchanged_codes = sorted(slots_already_ok.mapped('code'))

                if slots_to_update:
                    slots_to_update.sudo().write({'is_fault_blocked': slot_blocked})
                    should_notify = True

                machine_unblocked_cascade = False
                if status == 'SUCCESS' and processed_slot_codes and machine.is_fault_blocked:
                    machine.sudo().write({'is_fault_blocked': False})
                    should_notify = True
                    machine_unblocked_cascade = True

                if should_notify:
                    processing_result = 'processed'
                elif not valid_slots:
                    processing_result = 'processed'
                else:
                    processing_result = 'no_change'

                if invalid_slot_values or missing_slots:
                    processing_result = 'partial'

                # Construcción del resumen humano.
                deduction_note = (
                    f" (slot deducido, ventana {_ALARM_DEDUCTION_WINDOW_SECONDS}s)"
                    if deduced_slot_code is not None else ""
                )
                if slots_changed_codes:
                    verb = 'bloqueados' if slot_blocked else 'reactivados'
                    summary = (
                        f"Slots {slots_changed_codes} {verb}{deduction_note}"
                    )
                    if machine_unblocked_cascade:
                        summary += " + máquina reactivada"
                elif slots_unchanged_codes:
                    target = 'bloqueado' if slot_blocked else 'activo'
                    summary = (
                        f"Slot(s) {slots_unchanged_codes} ya estaban {target} — "
                        f"sin cambios{deduction_note}"
                    )
                elif missing_slots or invalid_slot_values:
                    summary = (
                        f"Slots no encontrados: {missing_slots}"
                        + (f", inválidos: {invalid_slot_values}"
                           if invalid_slot_values else "")
                    )
                else:
                    summary = f"Alarma SLOT {status} sin efecto"

            if should_notify:
                env['stock.quant'].sudo()._notify_vending_changes_for_machines(machine)

            actions.update({
                'processed_slot_codes': processed_slot_codes,
                'slots_changed_codes': slots_changed_codes,
                'slots_unchanged_codes': slots_unchanged_codes,
                'invalid_slot_values': invalid_slot_values,
                'missing_slots': missing_slots,
                'machine_is_fault_blocked': bool(machine.is_fault_blocked),
                'machine_fault_blocked_slots_count': machine.fault_blocked_slots_count,
                'bus_notified': should_notify,
            })

            self._log_webhook(
                machine_code,
                raw_body,
                'alarm',
                processing_result=processing_result,
                actions=actions,
                summary=summary,
            )

            response_payload = {
                'status': 'ok' if processing_result in ('processed', 'no_change') else 'partial',
                'machine': machine_code,
                'scope': scope,
                'result': processing_result,
                'machine_is_fault_blocked': bool(machine.is_fault_blocked),
                'machine_fault_blocked_slots_count': machine.fault_blocked_slots_count,
                'processed_slot_codes': processed_slot_codes,
                'slots_changed_codes': slots_changed_codes,
                'slots_unchanged_codes': slots_unchanged_codes,
                'missing_slots': missing_slots,
                'invalid_slot_values': invalid_slot_values,
                'summary': summary,
            }
            _logger.info(f"{LOG_SEP}")
            return self._make_json_response(response_payload, 200)

        except Exception as error:
            _logger.exception('[WEBHOOK ALARM] Error interno procesando')
            self._log_webhook(
                machine_code,
                raw_body,
                'alarm',
                error_msg=f'Internal error: {str(error)}',
                processing_result='internal_error',
            )
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
            result = self._process_single_load_item(env, request_obj, item, raw_body, label)
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

    def _process_single_load_item(self, env, request_obj, item, raw_body, label='item'):
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

            auth_error = self._authenticate_machine(machine, request_obj, f'{machine_code}/{slot_number}', raw_body, 'load')
            if auth_error:
                msg = 'Unauthorized'
                return {
                    'status': 'error',
                    'http_status': 401,
                    'message': msg,
                    'machine': machine_code,
                    'slot': slot_number,
                }

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
                     error_msg=None, processing_result=None, actions=None,
                     summary=None):
        """
        Registra el webhook en el log de auditoría.

        Campos específicos de alarma (machine_code, alarm_scope, alarm_status,
        deduced_slot_code) se toman de ``actions`` si vienen presentes; si no,
        se intenta parsearlos del ``raw_body`` para el endpoint ``alarm``.

        Args:
            reference: Referencia de la transacción
            raw_body: Body crudo del request
            endpoint_type: 'payment_status', 'delivery_status', 'alarm' o 'load'
            error_msg: Mensaje de error (si hubo)
            processing_result: 'processed', 'duplicate', 'late_arrival', etc.
            actions: Dict con acciones realizadas (se serializa a JSON compacto)
            summary: Línea humana que describe qué hizo Odoo
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

            if summary:
                vals['summary'] = summary[:255]

            # Enriquecer columnas de filtrado a partir de actions/payload.
            action_data = actions if isinstance(actions, dict) else {}
            payload_data = {}
            if raw_body:
                try:
                    parsed = json.loads(raw_body)
                    if isinstance(parsed, dict):
                        payload_data = parsed
                except (json.JSONDecodeError, ValueError):
                    payload_data = {}

            machine_code = (
                action_data.get('machine')
                or payload_data.get('machine')
                or ''
            )
            if machine_code:
                vals['machine_code'] = str(machine_code)[:64]

            if endpoint_type == 'alarm':
                scope = (action_data.get('scope') or payload_data.get('scope') or '')
                scope = str(scope).upper().strip()
                if scope in ('MACHINE', 'SLOT'):
                    vals['alarm_scope'] = scope

                status = (action_data.get('status') or payload_data.get('status') or '')
                status = str(status).upper().strip()
                if status in ('FAIL', 'SUCCESS'):
                    vals['alarm_status'] = status

                deduced = action_data.get('deduced_slot_code')
                if isinstance(deduced, int):
                    vals['deduced_slot_code'] = deduced

            request.env['vending.webhook.log'].sudo().create(vals)
        except Exception:
            _logger.exception(f"[WEBHOOK] Error creando log de auditoría")
