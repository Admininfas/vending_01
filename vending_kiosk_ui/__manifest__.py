# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Vending Kiosk UI',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Custom UI for Vending Kiosk',
    'description': """
        Interfaz de usuario para máquinas expendedoras.
        - Agrega modo 'vending' al self_ordering_mode
        - Botones en dashboard para abrir/cerrar sesión sin caja registradora
        - Filtro de productos según stock en slots
    """,
    'author': 'UTN',
    'depends': [
        'point_of_sale',
        'pos_self_order',
        'vending_kiosk_core',
        'vending_kiosk_provider',
    ],
    'data': [
        'views/pos_config_views.xml',
    ],
    'assets': {
        'pos_self_order.assets': [
            'vending_kiosk_ui/static/src/app/**/*.js',
            'vending_kiosk_ui/static/src/app/**/*.xml',
            'vending_kiosk_ui/static/src/app/**/*.scss',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'sequence': -101,
    'license': 'OPL-1',
}