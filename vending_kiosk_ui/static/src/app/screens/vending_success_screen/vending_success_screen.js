/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useSelfOrder } from "@pos_self_order/app/services/self_order_service";
import { VENDING_DEFAULTS, VendingScreenMixin } from "../vending_screen_mixin";

/**
 * Pantalla de éxito mostrada cuando el pago fue procesado correctamente.
 * Muestra un mensaje de confirmación y redirige automáticamente al menú principal.
 */
export class VendingSuccessScreen extends Component {
    static template = "vending_kiosk_ui.VendingSuccessScreen";
    static props = {
        product: { type: Object, optional: true },
        reference: { type: String, optional: true },
        close: { type: Function, optional: true }
    };
    
    setup() {
        // Servicios
        this.selfOrder = useSelfOrder();
        this.router = useService("router");
        
        // Props
        this.product = this.props.product;
        this.reference = this.props.reference;
        
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
    onClickGoToMenu() {
        this._goToMenu();
    }
}
