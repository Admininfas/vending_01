/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useSelfOrder } from "@pos_self_order/app/services/self_order_service";
import { 
    VENDING_DEFAULTS, 
    ERROR_MESSAGES,
    VendingScreenMixin 
} from "../vending_screen_mixin";

/**
 * Pantalla de error mostrada cuando hay problemas en el proceso de compra.
 * Muestra el mensaje de error y redirige automáticamente al menú principal.
 */
export class VendingErrorScreen extends Component {
    static template = "vending_kiosk_ui.VendingErrorScreen";
    static props = {
        product: { type: Object, optional: true },
        error: { type: String, optional: true },
        errorTitle: { type: String, optional: true },
        close: { type: Function, optional: true }
    };
    
    setup() {
        // Servicios
        this.selfOrder = useSelfOrder();
        this.router = useService("router");
        
        // Props con valores por defecto
        this.product = this.props.product;
        this.errorTitle = this.props.errorTitle || "Error";
        this.error = this.props.error || ERROR_MESSAGES.UNKNOWN_ERROR;
        this.genericMessage = ERROR_MESSAGES.GENERIC_HELP;
        
        // Estado reactivo
        const countdownSeconds = this.selfOrder?.config?.vending_countdown_seconds 
            || VENDING_DEFAULTS.COUNTDOWN_SECONDS;
        this.state = useState({
            countdown: countdownSeconds,
        });
        
        // Timer
        this._countdownTimer = null;

        onMounted(() => this._onMounted());
        onWillUnmount(() => this._onWillUnmount());
    }

    // ========================
    // Lifecycle
    // ========================

    _onMounted() {
        this._countdownTimer = setInterval(() => {
            if (this.state.countdown > 0) {
                this.state.countdown -= 1;
            }
            if (this.state.countdown <= 0) {
                this._goToMenu();
            }
        }, 1000);
    }

    _onWillUnmount() {
        if (this._countdownTimer) {
            clearInterval(this._countdownTimer);
            this._countdownTimer = null;
        }
    }

    // ========================
    // Computed Getters
    // ========================

    get formattedPrice() {
        return VendingScreenMixin.formatProductPrice(this.product, this.selfOrder);
    }

    get productTemplateId() {
        return VendingScreenMixin.getProductTemplateId(this.product);
    }

    // ========================
    // Navigation
    // ========================

    /**
     * Navega de vuelta al menú principal.
     */
    _goToMenu() {
        if (this._countdownTimer) {
            clearInterval(this._countdownTimer);
            this._countdownTimer = null;
        }
        VendingScreenMixin.navigateToMenu(this.props.close, this.router);
    }

    /**
     * Handler del botón volver.
     */
    regresar() {
        this._goToMenu();
    }
}