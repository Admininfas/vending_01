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
        productMeta: {},
        machineFaultBlocked: Boolean(selfOrder?.config?._vending_machine_fault_blocked),
        machineHasFaultBlockedSlots: Boolean(selfOrder?.config?._vending_machine_has_fault_blocked_slots),
        machineFaultBlockedSlotsCount: Number(selfOrder?.config?._vending_machine_fault_blocked_slots_count || 0),
    });

    let busChannel = null;
    let isActive = false;
    let pollTimer = null;
    let currentHash = '';

    // ── Helpers ──
    function _toTemplateId(product) {
        if (!product) {
            return null;
        }
        const maybeId = product.product_tmpl_id?.id ?? product.id;
        const numericId = Number(maybeId);
        return Number.isFinite(numericId) ? numericId : null;
    }

    function _iterProductCandidates(visitor) {
        const visited = new Set();
        const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

        const visit = (product) => {
            if (!product || typeof product !== "object") {
                return;
            }
            const looksLikeProduct = (
                hasOwn(product, "display_name")
                || hasOwn(product, "list_price")
                || hasOwn(product, "public_description")
                || hasOwn(product, "product_tmpl_id")
            );
            if (!looksLikeProduct) {
                return;
            }
            if (visited.has(product)) {
                return;
            }
            visited.add(product);
            visitor(product);
        };

        const modelContainers = [];
        const models = selfOrder?.models;

        if (Array.isArray(models)) {
            modelContainers.push(models);
        } else if (models && typeof models === "object") {
            const productModelKeys = new Set([
                "product.template",
                "product_template",
                "product.product",
                "product_product",
            ]);
            for (const [key, value] of Object.entries(models)) {
                if (!productModelKeys.has(key)) {
                    continue;
                }
                if (Array.isArray(value)) {
                    modelContainers.push(value);
                } else if (value && typeof value === "object") {
                    modelContainers.push(Object.values(value));
                }
            }
        }

        const extraCollections = [
            selfOrder?.products,
            selfOrder?.productTemplates,
            selfOrder?.productById && Object.values(selfOrder.productById),
        ];

        for (const collection of [...modelContainers, ...extraCollections]) {
            if (!collection) {
                continue;
            }
            if (Array.isArray(collection)) {
                for (const item of collection) {
                    visit(item);
                }
            }
        }

        visit(selfOrder?.selectedVendingProduct);
    }

    function _applyMetaToProduct(product, meta) {
        const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
        const normalizeOdooDateTime = (value) => {
            if (!value) {
                return false;
            }
            if (typeof value !== "string") {
                return false;
            }
            const trimmed = value.trim();
            if (!trimmed) {
                return false;
            }
            // Odoo web espera "YYYY-MM-DD HH:MM:SS" para datetime fields.
            const normalized = trimmed.replace("T", " ").split(".")[0];
            return normalized || false;
        };
        if (!product || !meta) {
            return;
        }

        if (hasOwn(meta, "display_name")) {
            product.display_name = meta.display_name || "";
            if (hasOwn(product, "name")) {
                product.name = meta.display_name || product.name || "";
            }
        }
        if (hasOwn(meta, "public_description")) {
            product.public_description = meta.public_description || false;
        }
        if (hasOwn(meta, "write_date")) {
            product.write_date = normalizeOdooDateTime(meta.write_date);
        }
        if (hasOwn(meta, "price")) {
            const parsedPrice = Number(meta.price);
            if (Number.isFinite(parsedPrice)) {
                product.list_price = parsedPrice;
                if (hasOwn(product, "lst_price")) {
                    product.lst_price = parsedPrice;
                }
                product._vending_price_override = parsedPrice;
            }
        }
    }

    function applyProductMeta(productMeta) {
        if (!productMeta || typeof productMeta !== "object") {
            return;
        }

        for (const key of Object.keys(vendingProducts.productMeta)) {
            delete vendingProducts.productMeta[key];
        }
        Object.assign(vendingProducts.productMeta, productMeta);

        _iterProductCandidates((product) => {
            const templateId = _toTemplateId(product);
            if (!templateId) {
                return;
            }

            const meta = productMeta[templateId] || productMeta[String(templateId)];
            if (!meta) {
                return;
            }

            _applyMetaToProduct(product, meta);
        });

        const selected = selfOrder?.selectedVendingProduct;
        const selectedTemplateId = _toTemplateId(selected);
        if (selectedTemplateId) {
            const selectedMeta = productMeta[selectedTemplateId] || productMeta[String(selectedTemplateId)];
            if (selectedMeta) {
                _applyMetaToProduct(selected, selectedMeta);
            }
        }
    }

    function updateProducts(newIds, newSlots, newProductMinSlotCode, newProductMeta, machineState = null) {
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
        if (newProductMeta) {
            applyProductMeta(newProductMeta);
        }
        if (machineState && typeof machineState === "object") {
            vendingProducts.machineFaultBlocked = Boolean(machineState.machineFaultBlocked);
            vendingProducts.machineHasFaultBlockedSlots = Boolean(machineState.machineHasFaultBlockedSlots);
            vendingProducts.machineFaultBlockedSlotsCount = Number(
                machineState.machineFaultBlockedSlotsCount || 0
            );
        }

        const snapshot = {
            availableIds: [...vendingProducts.availableIds],
            machineFaultBlocked: Boolean(vendingProducts.machineFaultBlocked),
            machineHasFaultBlockedSlots: Boolean(vendingProducts.machineHasFaultBlockedSlots),
            machineFaultBlockedSlotsCount: Number(vendingProducts.machineFaultBlockedSlotsCount || 0),
        };
        if (typeof onProductsUpdated === 'function') {
            onProductsUpdated(snapshot);
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
            updateProducts(msg.all_available_ids || [], null, null, null, {
                machineFaultBlocked: msg.machine_fault_blocked,
                machineHasFaultBlockedSlots: msg.machine_has_fault_blocked_slots,
                machineFaultBlockedSlotsCount: msg.machine_fault_blocked_slots_count,
            });

            // El bus avisa rápido; hacemos poll inmediato para traer metadata fresca.
            pollNow();
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
                resp.product_meta || null,
                {
                    machineFaultBlocked: resp.machine_fault_blocked,
                    machineHasFaultBlockedSlots: resp.machine_has_fault_blocked_slots,
                    machineFaultBlockedSlotsCount: resp.machine_fault_blocked_slots_count,
                },
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
