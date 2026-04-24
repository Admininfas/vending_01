/** @odoo-module **/

import { Component } from "@odoo/owl";
import { useSelfOrder } from "@pos_self_order/app/services/self_order_service";

/**
 * Pantalla de fuera de servicio para máquina vending desactivada por falla
 * o sin productos disponibles. La navegación de entrada/salida la maneja
 * el patch de selfOrderIndex (routes.js) en base al polling/bus root.
 */
export class VendingOutOfServiceScreen extends Component {
    static template = "vending_kiosk_ui.VendingOutOfServiceScreen";

    setup() {
        this.selfOrder = useSelfOrder();
    }
}
