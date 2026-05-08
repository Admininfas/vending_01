# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Vending Kiosk Provider',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Integración con proveedor de pagos y entrega física de productos para máquinas expendedoras',
    'description': """
        Módulo de integración con el proveedor externo de pagos y entregas físicas (Winfas) para vending.
        
        Funcionalidades:
        - Creación de órdenes POS y solicitud de QR de pago
        - Webhook POST /v1/vending/webhook/payment_status (estado de pago)
        - Webhook POST /v1/vending/webhook/delivery_status (estado de entrega)
        - Webhook POST /v1/vending/webhook/load (carga de stock en slots)
        - Polling de estado de órdenes desde el kiosk
        - Logging y auditoría de todos los webhooks recibidos
        - Dummy API interno para desarrollo/testing (reemplazable por API real)
    """,
    'author': 'UTN',
    'depends': [
        'point_of_sale',
        'vending_kiosk_core',
        'bus',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/vending_webhook_log_views.xml',
        'views/pos_order_views.xml',
        'views/pos_config_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'sequence': -103,
    'license': 'OPL-1',

}