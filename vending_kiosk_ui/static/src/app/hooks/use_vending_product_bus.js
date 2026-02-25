/** @odoo-module **/

import { useService, useBus } from "@web/core/utils/hooks";
import { onMounted, onWillUnmount } from "@odoo/owl";

/**
 * Hook personalizado para suscribirse a actualizaciones de productos vending vía bus.
 * 
 * Cuando cambia el stock de productos en la máquina (crear/modificar/eliminar stock.quant),
 * el backend envía una notificación al canal `vending_products_{config_id}`.
 * 
 * Usa `useBus` de `@web/core/utils/hooks` para gestionar automáticamente
 * el ciclo de vida del listener (addEventListener en mount, removeEventListener en unmount).
 * 
 * @param {Object} selfOrder - Servicio selfOrder con la configuración
 * @param {Function} onProductsUpdated - Callback a ejecutar cuando se actualicen los productos
 * @returns {Object} - Estado y métodos para manejar actualizaciones
 */
export function useVendingProductBus(selfOrder, onProductsUpdated) {
    const busService = useService("bus_service");
    const orm = useService("orm");
    
    let busChannel = null;
    let isActive = false;

    /**
     * Handler de notificaciones del bus.
     * useBus vincula automáticamente el listener al ciclo de vida del componente.
     * Recibe notificaciones en el formato: { detail: [notifications] }
     */
    function onBusNotification({ detail: notifications }) {
        console.log("[Vending Bus] Notificación recibida:", notifications);
        if (!notifications || !Array.isArray(notifications)) {
            return;
        }

        // Filtrar notificaciones de nuestro canal
        const relevantNotifications = notifications.filter(
            notif => notif.payload?.channel === busChannel
        );

        if (relevantNotifications.length === 0) {
            return;
        }

        // Procesar cada notificación relevante
        for (const notif of relevantNotifications) {
            const message = notif.payload;
            
            if (message.type !== 'vending_products_update') {
                continue;
            }

            console.log(
                `[Vending Bus] Actualización recibida: ${message.machine_name || 'Máquina'}`
            );
            
            applyProductDelta(message);
        }
    }

    /**
     * Aplica actualización incremental de productos (DELTA UPDATE).
     * El backend envía la lista completa en all_available_ids.
     * El frontend calcula qué cambió comparando con su estado local.
     */
    function applyProductDelta(message) {
        if (!selfOrder?.config) {
            console.warn("[Vending Bus] No se puede aplicar delta: config no disponible");
            return;
        }

        try {
            const previousProducts = selfOrder.config._vending_available_products || [];
            const newProducts = message.all_available_ids || [];
            
            const previousSet = new Set(previousProducts);
            const added = newProducts.filter(id => !previousSet.has(id));
            const removed = previousProducts.filter(id => !new Set(newProducts).has(id));
            
            if (added.length > 0 || removed.length > 0) {
                console.log(
                    `[Vending Bus] Delta: +${added.length} -${removed.length} | ` +
                    `Total: ${newProducts.length} productos`
                );
            }
            
            selfOrder.config._vending_available_products = newProducts;

            if (onProductsUpdated && typeof onProductsUpdated === 'function') {
                onProductsUpdated(newProducts);
            }
            
        } catch (error) {
            console.error("[Vending Bus] Error aplicando delta:", error);
            reloadProducts();
        }
    }

    /**
     * Recarga los productos disponibles desde el backend (fallback).
     */
    async function reloadProducts() {
        if (!selfOrder?.config?.id) {
            console.warn("[Vending Bus] No se puede recargar: config no disponible");
            return;
        }

        try {
            console.log("[Vending Bus] Recargando productos disponibles...");
            
            const updatedProductIds = await orm.call(
                'pos.config',
                'get_available_vending_product_ids',
                [selfOrder.config.id]
            );

            if (!updatedProductIds || !Array.isArray(updatedProductIds)) {
                console.error("[Vending Bus] Respuesta inválida del servidor");
                return;
            }
            
            selfOrder.config._vending_available_products = updatedProductIds;
            
            console.log(
                `[Vending Bus] Productos actualizados: ${updatedProductIds.length} disponibles`
            );

            if (onProductsUpdated && typeof onProductsUpdated === 'function') {
                onProductsUpdated(updatedProductIds);
            }
            
        } catch (error) {
            console.error("[Vending Bus] Error recargando productos:", error);
        }
    }

    // ── useBus: gestiona addEventListener/removeEventListener automáticamente ──
    useBus(busService, "notification", onBusNotification);

    // ── Gestión de canal: addChannel en mount, deleteChannel en unmount ──
    onMounted(() => {
        if (!selfOrder?.config?.id) {
            console.warn("[Vending Bus] No se pudo suscribir: config no disponible");
            return;
        }
        if (selfOrder.config.self_ordering_mode !== 'vending') {
            return;
        }

        busChannel = `vending_products_${selfOrder.config.id}`;
        busService.addChannel(busChannel);
        isActive = true;
        console.log(`[Vending Bus] Suscrito al canal: ${busChannel}`);
    });

    onWillUnmount(() => {
        if (busChannel) {
            busService.deleteChannel(busChannel);
            console.log(`[Vending Bus] Desuscrito del canal: ${busChannel}`);
        }
        busChannel = null;
        isActive = false;
    });

    return {
        isActive,
        reloadProducts,
    };
}
