/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { selfOrderIndex } from "@pos_self_order/app/self_order_index";
import { VendingProcessingScreen } from "./screens/vending_processing_screen/vending_processing_screen";
import { VendingSuccessScreen } from "./screens/vending_success_screen/vending_success_screen";
import { VendingErrorScreen } from "./screens/vending_error_screen/vending_error_screen";

patch(selfOrderIndex, {
    components: {
        ...selfOrderIndex.components,
        VendingProcessingScreen,
        VendingSuccessScreen,
        VendingErrorScreen,
    },
});