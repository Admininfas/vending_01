# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Vending Kiosk Provider',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Webhooks DUMMY para proveedores de máquinas expendedoras',
    'description': """
        Módulo proveedor DUMMY para webhooks de máquinas expendedoras.
        
        Funcionalidades:
        - Webhook POST /v1/vending/webhook/status (estado de transacciones)
        - Webhook POST /v1/vending/webhook/load (carga de stock en slots)
        - Logging completo de todos los requests en modelo vending.webhook.log
        - Vista tree accesible desde menú de Punto de Venta
        - Validaciones suaves con warnings (no rechazo)
        
        NOTA: Esta es una versión DUMMY para desarrollo/testing.
        No implementa lógica de negocio real ni seguridad.
    """,
    'author': 'UTN',
    'depends': [
        'point_of_sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/vending_webhook_log_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'sequence': -100,
}