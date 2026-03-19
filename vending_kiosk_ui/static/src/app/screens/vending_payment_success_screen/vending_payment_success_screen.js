/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService, useBus } from "@web/core/utils/hooks";
import { useSelfOrder } from "@pos_self_order/app/services/self_order_service";
import { rpc } from "@web/core/network/rpc";
import { ERROR_MESSAGES, ERROR_TITLES, inferErrorTitle, VendingScreenMixin } from "../vending_screen_mixin";

const DELIVERY_SUCCESS_STATUSES = new Set(['success', 'SUCCESS', 'vending_delivery_success']);
const PAYMENT_SUCCESS_STATUSES = new Set(['payment_success']);
const ERROR_STATUSES = new Set(['error', 'ERROR', 'payment_error', 'vending_delivery_error']);
const PAYMENT_WAIT_TIMEOUT_SECONDS = 120;
const PAYMENT_WAIT_TIMEOUT_ERROR = "No se pudo confirmar la entrega en 2 minutos. Tu pago sera revisado por el operador.";

export class VendingPaymentSuccessScreen extends Component {
    static template = "vending_kiosk_ui.VendingPaymentSuccessScreen";
    static props = {
        product: { type: Object, optional: true },
        reference: { type: String, optional: true },
        close: { type: Function, optional: true },
    };

    setup() {
        this.selfOrder = useSelfOrder();
        this.router = useService("router");
        this.busService = useService("bus_service");

        this.product = this.props.product;
        this.reference = this.props.reference || this.selfOrder?.vendingReference || null;

        this.state = useState({
            elapsedSeconds: 0,
            busActive: false,
        });

        this._busChannel = null;
        this._elapsedTimer = null;
        this._statusPollTimer = null;
        this._hasExited = false;

        useBus(this.busService, "notification", this._onBusNotification.bind(this));

        onMounted(() => this._onMounted());
        onWillUnmount(() => this._onWillUnmount());
    }

    _onMounted() {
        this._startElapsedTimer();
        this._subscribeToBus();
        this._startStatusPolling();
    }

    _onWillUnmount() {
        this._unsubscribeFromBus();
        this._stopPolling();
        this._stopElapsedTimer();
    }

    _startElapsedTimer() {
        this._elapsedTimer = setInterval(() => {
            this.state.elapsedSeconds += 1;
            if (this.state.elapsedSeconds >= PAYMENT_WAIT_TIMEOUT_SECONDS) {
                this._showErrorScreen(PAYMENT_WAIT_TIMEOUT_ERROR, ERROR_TITLES.DEFAULT);
            }
        }, 1000);
    }

    _stopElapsedTimer() {
        if (this._elapsedTimer) {
            clearInterval(this._elapsedTimer);
            this._elapsedTimer = null;
        }
    }

    _subscribeToBus() {
        if (!this.reference || !this.busService) {
            return;
        }

        this._busChannel = `vending_order_${this.reference}`;
        this.busService.addChannel(this._busChannel);
        this.state.busActive = true;
    }

    _unsubscribeFromBus() {
        if (this._busChannel && this.busService) {
            this.busService.deleteChannel(this._busChannel);
        }
        this._busChannel = null;
        this.state.busActive = false;
    }

    _onBusNotification({ detail: notifications }) {
        if (!notifications || !Array.isArray(notifications) || !this._busChannel) {
            return;
        }

        const relevantNotifications = notifications.filter(
            notif => notif.payload?.channel === this._busChannel
        );

        relevantNotifications.forEach(notif => {
            const message = notif.payload;
            if (message.type !== 'vending_payment_result') {
                return;
            }
            if (message.reference !== this.reference) {
                return;
            }
            this._applyStatusTransition(message.status, {
                description: message.description || '',
                errorTypeLabel: ERROR_TITLES.DEFAULT,
            });
        });
    }

    _startStatusPolling() {
        if (!this.reference) {
            return;
        }

        const pollingInterval = this.state.busActive ? 5000 : 3000;
        this._statusPollTimer = setInterval(async () => {
            await this._checkOrderStatus();
        }, pollingInterval);
    }

    _stopPolling() {
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
            this._statusPollTimer = null;
        }
    }

    async _checkOrderStatus() {
        if (!this.reference) {
            return;
        }

        try {
            const response = await rpc("/v1/vending/order/status", {
                reference: this.reference,
            });

            if (!response?.found) {
                this._stopPolling();
                return;
            }

            const status = response.vending_status || response.status;
            this._applyStatusTransition(status, {
                description: response.error_description || '',
                errorTypeLabel: response.error_type_label || ERROR_TITLES.DEFAULT,
            });
        } catch (error) {
            // Keep polling on transient connectivity issues.
        }
    }

    _applyStatusTransition(rawStatus, { description = '', errorTypeLabel = ERROR_TITLES.DEFAULT } = {}) {
        if (this._hasExited) {
            return;
        }

        const status = String(rawStatus || '').trim();
        if (!status) {
            return;
        }

        if (DELIVERY_SUCCESS_STATUSES.has(status)) {
            this._navigateToDeliverySuccess();
            return;
        }

        if (PAYMENT_SUCCESS_STATUSES.has(status)) {
            return;
        }

        if (ERROR_STATUSES.has(status)) {
            const errorTitle = inferErrorTitle(description) || errorTypeLabel;
            this._showErrorScreen(description || ERROR_MESSAGES.UNKNOWN_ERROR, errorTitle);
        }
    }

    _navigateToDeliverySuccess() {
        if (this._hasExited) {
            return;
        }
        this._hasExited = true;

        this._unsubscribeFromBus();
        this._stopPolling();
        this._stopElapsedTimer();

        if (this.router) {
            this.router.navigate("vending-success");
        }
    }

    _showErrorScreen(errorMessage, errorTitle = ERROR_TITLES.DEFAULT) {
        if (this._hasExited) {
            return;
        }
        this._hasExited = true;

        this._unsubscribeFromBus();
        this._stopPolling();
        this._stopElapsedTimer();

        if (this.selfOrder) {
            this.selfOrder.vendingErrorMessage = errorMessage;
            this.selfOrder.vendingErrorTitle = errorTitle;
            this.selfOrder.selectedVendingProduct = this.product;
        }

        if (this.router) {
            this.router.navigate("vending-error");
        }
    }

    get formattedPrice() {
        return VendingScreenMixin.formatProductPrice(this.product, this.selfOrder);
    }

    get waitTimeLabel() {
        return VendingScreenMixin.formatTime(this.state.elapsedSeconds);
    }
}
