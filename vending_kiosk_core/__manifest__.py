# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Vending Kiosk Core',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Infrastructure for automated vending machines integrated with POS',
    'description': """
Modelos base para máquinas expendedoras automáticas integradas con Odoo POS.

Características principales:
- Gestión de máquinas expendedoras con configuración de hardware y almacén
- Definición de slots con productos y ubicaciones específicas
- Procesamiento completo de transacciones de vending (QR, pago, facturación, stock)
- Integración con webhooks desde hardware externo
- Estados de transacción independientes del flujo POS estándar
- Rastreo automático de cambios en configuraciones
- Expiración automática de códigos QR mediante cron job
- Validaciones de integridad (máquina única por POS, slot único por código, etc.)
- Control de acceso basado en dos grupos: Viewer (lectura) y Admin (CRUD completo)

Extensiones:
- pos.order: Campos de vending (estado, máquina, slot, webhook, error log)
- pos.config: Relación con máquina, métodos auxiliares para productos
- stock.picking: Marcado de entregas generadas por vending
- account.move: Marcado de facturas generadas por vending
- stock.quant, stock.location, stock.warehouse: Validaciones vending
    """,
    'author': 'UTN',
    'depends': [
        'point_of_sale',
        'pos_self_order',
        'stock',
        'account',
        'mail',
        'bus',
    ],
    'data': [
        'security/vending_groups.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/vending_machine_views.xml',
        'views/vending_slot_views.xml',
        'views/stock_location_views.xml',
        'views/stock_warehouse_views.xml',
        'views/pos_config_views.xml',        
        'views/pos_order_views.xml',
        'views/vending_traceability_views.xml',
        'views/vending_menu.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'sequence': -112,
    'license': 'OPL-1',

}
