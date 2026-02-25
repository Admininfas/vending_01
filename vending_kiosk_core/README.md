# Vending Kiosk Core

Módulo Odoo 19 que proporciona los modelos y funcionalidades base para máquinas expendedoras (vending machines) integradas con el sistema POS.

## Descripción

Este addon implementa la infraestructura fundamental para gestionar máquinas expendedoras automáticas, permitiendo:

- **Configuración de máquinas**: Gestión de máquinas expendedoras con su identificación única, almacén asociado y punto de venta
- **Gestión de slots**: Definición de posiciones físicas en cada máquina con productos específicos y ubicaciones de stock
- **Procesamiento de transacciones**: Manejo del flujo completo de venta (QR, pago, facturación, movimiento de stock)
- **Webhook integration**: Procesamiento de eventos desde hardware externo con estados de transacción
- **Auditoría y rastreo**: Registro automático de cambios en configuraciones y transacciones

## Modelos principales

### vending.machine
Máquina expendedora física con:
- Identificador único de hardware
- Configuración de POS asociada
- Almacén de stock
- Métodos de pago
- Cliente anónimo para transacciones
- Diarios contables para facturación
- Timeouts de QR configurables

### vending.slot
Posición individual dentro de una máquina con:
- Producto asignado
- Ubicación de stock específica
- Número de slot único por máquina
- Stock actual computado desde movimientos reales
- Estado activo/inactivo

### Extensiones de modelos

**pos.order**: Agrega campos de vending:
- `vending_reference`: ID único de transacción
- `vending_status`: Estado del flujo (draft, qr_ready, qr_expired, payment_success, vending_delivery_success, payment_error, vending_delivery_error)
- `vending_machine_id` y `vending_slot_id`: Referencias a máquina y slot
- `vending_webhook_received_at`: Timestamp de recepción del webhook
- `vending_error_description`: Errores reportados por la máquina (webhook ERROR)
- `vending_internal_error`: Errores internos de Odoo (pago, factura, stock)
- `vending_delivery_id`: Enlace al albarán (stock.picking) creado

**pos.config**: Extiende con:
- `vending_machine_id`: Relación bidireccional con máquina
- Métodos para obtener productos disponibles y mejor slot

**stock.picking**: Agrega:
- `is_vending_delivery`: Campo booleano para marcar entregas desde vending

**account.move**: Agrega:
- `is_vending_invoice`: Campo booleano para marcar facturas de vending

**stock.quant** y **stock.location**: Extensiones para validación de consistencia

## Funcionalidades clave

### Procesamiento de webhooks
Maneja eventos desde máquinas (éxito/error) ejecutando:
1. Validación de duplicados (previene reproceso)
2. Para SUCCESS: Creación de pago, factura, movimiento de stock, marcación como entregado
3. Para ERROR: Marcación del tipo de error (pago/entrega) con descripción
4. Separación clara entre errores reales (webhook) vs errores internos (Odoo)

**Filosofía de manejo de errores:**
- Si el webhook dice SUCCESS: la máquina YA despachó, aunque Odoo falle internamente
  - `vending_status = 'vending_delivery_success'` (usuario ve éxito)
  - Error interno se registra en `vending_internal_error` (admin lo ve)
- Si el webhook dice ERROR: es un error real de la máquina
  - `vending_status = 'payment_error'` o `'vending_delivery_error'`
  - Descripción en `vending_error_description`

### Métodos de soporte para error handling
- `_check_webhook_duplicate()`: Detecta webhooks duplicados automáticamente
- `_register_internal_error(error_name, desc)`: Registra errores sin cambiar estado, agrega mensaje en chatter
- `_process_vending_payment_and_invoice()`: Procesa pago/factura, retorna bool, captura errores internos
- `_process_vending_stock_movement()`: Procesa stock, asigna vending_delivery_id, captura errores
- `process_vending_success_webhook()`: Orquestador con try-catch independientes, SIEMPRE retorna True

### Expiración automática de QR
Cron job diario que marca como expirados los QRs que superan el timeout configurado.

### Validaciones
- Máquina única por almacén
- Máquina única por punto de venta
- Slot único por código dentro de máquina
- Ubicación única por slot
- Configuración vending completa (diario, método pago, cliente anónimo)

### Rastreo de cambios
Todos los campos críticos registran cambios automáticamente para auditoría.

## Dependencias

- `point_of_sale`: Módulo POS de Odoo
- `pos_self_order`: Extensión POS
- `stock`: Gestión de inventario
- `account`: Módulo contable
- `mail`: Para auditoría en chatter de máquinas y slots

## Seguridad

Define dos grupos de usuarios:
- **Vending Manager - Viewer**: Lectura de máquinas y slots
- **Vending Manager - Admin**: Control total sobre configuración y transacciones

Implementa ACLs granulares por modelo y grupo con permisos CRUD diferenciados.

## Estructura de archivos

```
models/
  vending_machine.py      # Modelo principal de máquinas
  vending_slot.py         # Posiciones dentro de máquinas
  pos_order.py            # Extensión de órdenes POS
  pos_config.py           # Métodos auxiliares POS
  pos_payment.py          # Extensión de pagos
  stock_picking.py        # Marcado de entregas vending
  stock_quant.py          # Validación de stock
  stock_location.py       # Extensión de ubicaciones
  stock_warehouse.py      # Extensión de almacenes
  account_move.py         # Marcado de facturas vending
  product_template_vending.py  # Extensión de productos

security/
  vending_groups.xml      # Definición de grupos
  ir.model.access.csv     # ACLs por modelo y grupo

data/
  ir_cron.xml             # Job automático de expiración QR

views/
  vending_machine_views.xml
  vending_slot_views.xml
  pos_order_views.xml
  pos_config_views.xml
  vending_menu.xml
  (+ otras vistas)
```

## Instalación

1. Ubica el addon en la carpeta de addons
2. Actualiza la lista de módulos: `Aplicaciones > Actualizar lista de aplicaciones`
3. Instala el módulo: `Aplicaciones > Vending Kiosk Core`
4. Configura máquinas e slots en el menú Vending

## Configuración típica

1. Crear máquina expendedora con:
   - Nombre y código único
   - Punto de venta (POS)
   - Almacén dedicado
   - Método de pago (QR)
   - Diario de facturación
   - Cliente anónimo

2. Crear slots con:
   - Número (00-99)
   - Producto
   - Ubicación de stock

3. Sincronizar IDs de máquina/slot con hardware

4. Configurar webhook receiver en el frontend para procesar eventos

## Notas técnicas

- Los stocks se calculan en tiempo real desde `stock.quant` con disponibilidad actual
- Las transacciones de vending se mantienen separadas en estado `vending_status` 
- Los movimientos de stock usan pickings estándar de Odoo para trazabilidad completa
- Las facturas se generan automáticamente con el diario específico de la máquina
- Auditoría completa: tracking automático en máquinas/slots y chatter para errores internos
- Sin uso de savepoints/transactions: cada proceso es independiente (uno falla, otros continúan)
