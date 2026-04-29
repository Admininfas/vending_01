/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { selfOrderIndex } from "@pos_self_order/app/self_order_index";
import { useService } from "@web/core/utils/hooks";
import { onMounted } from "@odoo/owl";
import { VendingProcessingScreen } from "./screens/vending_processing_screen/vending_processing_screen";
import { VendingSuccessScreen } from "./screens/vending_success_screen/vending_success_screen";
import { VendingPaymentSuccessScreen } from "./screens/vending_payment_success_screen/vending_payment_success_screen";
import { VendingErrorScreen } from "./screens/vending_error_screen/vending_error_screen";
import { VendingOutOfServiceScreen } from "./screens/vending_out_of_service_screen/vending_out_of_service_screen";
import { useVendingProductBus } from "./hooks/use_vending_product_bus";

// Rutas del flujo vending donde NO debemos interrumpir con una navegación a
// out-of-service: el usuario está en medio de un pago o ya llegó al resultado.
const VENDING_IN_PROGRESS_ROUTES = [
    "vending-processing",
    "vending-process",
    "vending-success",
    "vending-payment-success",
    "vending-error",
];

function _isInProgressRoute() {
    const path = (typeof window !== "undefined" && window.location?.pathname) || "";
    return VENDING_IN_PROGRESS_ROUTES.some((name) => path.endsWith(`/${name}`));
}

function _isOnOutOfService() {
    const path = (typeof window !== "undefined" && window.location?.pathname) || "";
    return path.endsWith("/vending-out-of-service");
}

patch(selfOrderIndex, {
    components: {
        ...selfOrderIndex.components,
        VendingProcessingScreen,
        VendingSuccessScreen,
        VendingPaymentSuccessScreen,
        VendingErrorScreen,
        VendingOutOfServiceScreen,
    },

    setup() {
        super.setup();
        this.router = useService("router");

        if (this.selfOrder?.config?.self_ordering_mode === "vending") {
            // Montamos el hook a nivel raíz: garantiza polling + bus activos
            // aunque el catálogo esté vacío y no haya ninguna screen vending
            // todavía instanciada. El callback decide la ruta correcta.
            useVendingProductBus(this.selfOrder, (snapshot) => {
                this._routeForVendingState(snapshot);
            });

            onMounted(() => {
                this._routeForVendingState({
                    machineFaultBlocked: Boolean(
                        this.selfOrder?.config?._vending_machine_fault_blocked
                    ),
                    availableIds: [
                        ...(this.selfOrder?.config?._vending_available_products || []),
                    ],
                });
            });
        }
    },

    _routeForVendingState(snapshot) {
        if (this.selfOrder?.config?.self_ordering_mode !== "vending") {
            return;
        }
        const posConfigId = this.selfOrder?.config?.id;
        if (!posConfigId) {
            return;
        }

        const machineBlocked = Boolean(snapshot?.machineFaultBlocked);
        const hasProducts =
            Array.isArray(snapshot?.availableIds) && snapshot.availableIds.length > 0;
        const shouldShowOutOfService = machineBlocked || !hasProducts;

        if (shouldShowOutOfService) {
            if (_isInProgressRoute() || _isOnOutOfService()) {
                return;
            }
            this.router.navigate(`/pos-self/${posConfigId}/vending-out-of-service`);
            return;
        }

        if (_isOnOutOfService()) {
            this.router.navigate("default");
        }
    },

    /**
     * En modo vending siempre permitimos que cargue el Router. Si no hay
     * productos disponibles (por fault_blocked, stock 0, slots bloqueados,
     * etc.) se navegará a VendingOutOfServiceScreen desde _routeForVendingState
     * en lugar de mostrar el cartel nativo de Odoo.
     */
    get selfIsReady() {
        if (this.selfOrder?.config?.self_ordering_mode === "vending") {
            return true;
        }
        return super.selfIsReady;
    },
});
