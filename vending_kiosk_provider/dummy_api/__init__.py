# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""
DUMMY API Module - Simula la API de Winfas para testing local.

IMPORTANTE: Este módulo es solo para desarrollo y testing.
Cuando se integre con la API real de Winfas, eliminar esta carpeta
y actualizar vending_provider_client.py para usar los endpoints reales.

Endpoints simulados:
- POST /dummy/payments/qr/<machine> - Genera QR de pago
- GET /dummy/payments/qr/<uuid> - Retorna imagen del QR
- POST /dummy/status/<reference> - Consulta estado de referencia
"""

from . import dummy_provider_controller
