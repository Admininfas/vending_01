/** @odoo-module **/

import { useService, useBus } from "@web/core/utils/hooks";
import { onMounted, onWillUnmount, useState } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";

/** Polling interval in ms – balance between freshness and server load */
const POLL_INTERVAL_MS = 15000;

/**
 * Hook personalizado para suscribirse a actualizaciones de productos vending.
 *
 * Estrategia dual:
 *  1. **bus.bus** (instantáneo) – escucha canal `vending_products_{config_id}`.
 *  2. **Polling ligero** (fallback) – cada 15 s llama `/v1/vending/products/poll`
 *     con un hash; solo recarga datos si el hash cambió.
 *
 * Devuelve un estado reactivo OWL con los productos disponibles.
 * Nunca muta selfOrder.config (es un proxy reactive read-only).
 *
 * @param {Object}   selfOrder          Servicio selfOrder con la configuración
 * @param {Function} onProductsUpdated  Callback cuando cambian productos
 * @returns {{ vendingProducts: Object, isActive: boolean, reloadProducts: Function }}
 */
export function useVendingProductBus(selfOrder, onProductsUpdated) {
    const busService = useService("bus_service");

    // Estado reactivo local – fuente de verdad para productos disponibles.
    // Inicializado con los valores que el backend inyectó en config.
    const vendingProducts = useState({
        availableIds: [...(selfOrder?.config?._vending_available_products || [])],
        productSlots: Object.assign({}, selfOrder?.config?._vending_product_slots || {}),
        productMinSlotCode: Object.assign({}, selfOrder?.config?._vending_product_min_slot_code || {}),
    });

    let busChannel = null;
    let isActive = false;
    let pollTimer = null;
    let currentHash = '';

    // ── Helpers ──
    function updateProducts(newIds, newSlots, newProductMinSlotCode) {
        vendingProducts.availableIds.splice(0, vendingProducts.availableIds.length, ...newIds);
        if (newSlots) {
            for (const key of Object.keys(vendingProducts.productSlots)) {
                delete vendingProducts.productSlots[key];
            }
            Object.assign(vendingProducts.productSlots, newSlots);
        }
        if (newProductMinSlotCode) {
            for (const key of Object.keys(vendingProducts.productMinSlotCode)) {
                delete vendingProducts.productMinSlotCode[key];
            }
            Object.assign(vendingProducts.productMinSlotCode, newProductMinSlotCode);
        }
        if (typeof onProductsUpdated === 'function') {
            onProductsUpdated(newIds);
        }
    }

    // ── Bus handler ──
    function onBusNotification({ detail: notifications }) {
        if (!notifications || !Array.isArray(notifications)) {
            return;
        }
        const relevant = notifications.filter(
            n => n.payload?.channel === busChannel
        );
        if (!relevant.length) {
            return;
        }

        for (const notif of relevant) {
            const msg = notif.payload;
            if (msg.type !== 'vending_products_update') {
                continue;
            }
            // console.log(
            //     `[Vending Bus] Actualización recibida: ${msg.machine_name || 'Máquina'} ` +
            //     `(${(msg.all_available_ids || []).length} productos)`
            // );
            updateProducts(msg.all_available_ids || [], null, null);
        }
    }

    // ── Polling ──
    async function pollNow() {
        if (!selfOrder?.config?.id) {
            return;
        }
        try {
            const resp = await rpc("/v1/vending/products/poll", {
                pos_config_id: selfOrder.config.id,
                current_hash: currentHash,
            });

            if (resp.error) {
                // console.warn("[Vending Poll] Error from server:", resp.error);
                return;
            }

            currentHash = resp.hash || '';

            if (!resp.changed) {
                return;
            }

            // console.log(
            //     `[Vending Poll] Cambio detectado – ${(resp.product_ids || []).length} productos`
            // );

            updateProducts(
                resp.product_ids || [],
                resp.product_slots || null,
                resp.product_min_slot_code || null,
            );
        } catch (err) {
            // console.warn("[Vending Poll] Error de red:", err);
        }
    }

    function startPolling() {
        stopPolling();
        pollNow();
        pollTimer = setInterval(pollNow, POLL_INTERVAL_MS);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    // ── Lifecycle ──
    useBus(busService, "notification", onBusNotification);

    onMounted(() => {
        if (!selfOrder?.config?.id) {
            // console.warn("[Vending Bus] No se pudo suscribir: config no disponible");
            return;
        }
        if (selfOrder.config.self_ordering_mode !== 'vending') {
            return;
        }

        busChannel = `vending_products_${selfOrder.config.id}`;
        busService.addChannel(busChannel);
        isActive = true;
        // console.log(`[Vending Bus] Suscrito al canal: ${busChannel}`);

        startPolling();
        // console.log(`[Vending Poll] Polling iniciado (cada ${POLL_INTERVAL_MS / 1000}s)`);
    });

    onWillUnmount(() => {
        if (busChannel) {
            busService.deleteChannel(busChannel);
            // console.log(`[Vending Bus] Desuscrito del canal: ${busChannel}`);
        }
        busChannel = null;
        isActive = false;
        stopPolling();
    });

    return {
        vendingProducts,
        isActive,
        reloadProducts: pollNow,
    };
}
