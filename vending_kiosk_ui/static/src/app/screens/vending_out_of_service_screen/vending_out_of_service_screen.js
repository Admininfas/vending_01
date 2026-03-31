/** @odoo-module **/

import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useSelfOrder } from "@pos_self_order/app/services/self_order_service";
import { useVendingProductBus } from "../../hooks/use_vending_product_bus";

/**
 * Pantalla de fuera de servicio para máquina vending desactivada por falla.
 * Permanece visible hasta que la máquina vuelva a estar operativa.
 */
export class VendingOutOfServiceScreen extends Component {
    static template = "vending_kiosk_ui.VendingOutOfServiceScreen";

    setup() {
        this.selfOrder = useSelfOrder();
        this.router = useService("router");

        this.vendingBus = useVendingProductBus(this.selfOrder, (snapshot) => {
            if (!snapshot?.machineFaultBlocked) {
                this.router.navigate("default");
            }
        });
    }
}
