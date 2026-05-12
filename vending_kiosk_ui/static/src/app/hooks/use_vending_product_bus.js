/** @odoo-module **/

import { useService } from "@web/core/utils/hooks";
import { useEffect, useState } from "@odoo/owl";
import { _buildVendingInitialState } from "../routes";

/**
 * Wrapper de compatibilidad sobre el service `vending_product` (registrado en
 * routes.js).
 *
 * La lógica original del hook se migró a un service para sobrevivir a
 * re-renders del root y al cartel "Hey, looks like..." nativo. Este wrapper
 * mantiene la firma y la forma de retorno previas (`{ vendingProducts,
 * isActive, reloadProducts }`) para no romper consumidores actuales.
 *
 * Defensa adicional: si el service no fue iniciado todavía (por ejemplo, si
 * el patch del root no llegó a llamar `start()`), arrancamos acá usando los
 * datos del `selfOrder` recibido. `startService` es idempotente.
 */
export function useVendingProductBus(selfOrder, onProductsUpdated) {
    const svc = useService("vending_product");

    if (!svc.state.posConfigId && selfOrder?.config?.id) {
        try {
            svc.start({
                posConfigId: selfOrder.config.id,
                selfOrder,
                initial: _buildVendingInitialState(selfOrder),
            });
        } catch (err) {
            console.error("[vending_product] hook fallback start failed:", err);
        }
    }

    const state = useState(svc.state);

    if (typeof onProductsUpdated === "function") {
        useEffect(
            () => {
                onProductsUpdated({
                    availableIds: [...state.availableIds],
                    machineFaultBlocked: Boolean(state.machineFaultBlocked),
                    machineHasFaultBlockedSlots: Boolean(state.machineHasFaultBlockedSlots),
                    machineFaultBlockedSlotsCount: Number(state.machineFaultBlockedSlotsCount || 0),
                });
            },
            () => [
                state.availableIds.length,
                state.machineFaultBlocked,
            ],
        );
    }

    return {
        vendingProducts: state,
        isActive: true,
        reloadProducts: svc.reload,
    };
}
