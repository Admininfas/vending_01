/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService, useBus } from "@web/core/utils/hooks";
import { useSelfOrder } from "@pos_self_order/app/services/self_order_service";
import { rpc } from "@web/core/network/rpc";
import { qrCodeSrc } from "@point_of_sale/utils";
import { 
    VENDING_DEFAULTS, 
    ERROR_MESSAGES, 
    ERROR_TITLES,
    inferErrorTitle,
    VendingScreenMixin 
} from "../vending_screen_mixin";

const DELIVERY_SUCCESS_STATUSES = new Set(['success', 'SUCCESS', 'vending_delivery_success']);
const PAYMENT_SUCCESS_STATUSES = new Set(['payment_success']);
const ERROR_STATUSES = new Set(['error', 'ERROR', 'payment_error', 'vending_delivery_error']);

/**
 * Pantalla de procesamiento de pago para vending.
 * Muestra el QR de pago y realiza polling del estado hasta que se complete.
 * 
 * Flujo:
 * 1. Se carga el QR llamando al backend
 * 2. Se inicia un contador regresivo del timeout configurado
 * 3. Se realiza polling del estado cada 3 segundos
 * 4. Según el resultado se navega a éxito o error
 */
export class VendingProcessingScreen extends Component {
    static template = "vending_kiosk_ui.VendingProcessingScreen";
    static props = {
        product: { type: Object, optional: true },
        close: { type: Function, optional: true }
    };
    
    setup() {
        // Servicios
        this.selfOrder = useSelfOrder();
        this.router = useService("router");
        this.orm = useService("orm");
        this.busService = useService("bus_service");
        
        // Props
        this.product = this.props.product;
        
        // Estado reactivo
        this.state = useState({
            showBack: true,
            loading: true,
            error: null,
            qrUrl: null,
            qrContent: null,
            qrTimeout: 0,
            qrRemaining: 0,
            reference: null,
            paymentStatus: 'pending', // pending, success, error
            errorShown: false,
            busActive: false, // Indica si el bus está funcionando
            showCancelConfirmation: false, // Modal de confirmación de cancelación
            selectedSlot: null, // Slot seleccionado para este producto
        });
        
        // Referencias a timers para limpieza
        this._backTimer = null;
        this._qrTimer = null;
        this._statusPollTimer = null;
        this._busChannel = null;

        // useBus: gestiona addEventListener/removeEventListener automáticamente.
        // El listener se activa al montar y se remueve al desmontar.
        // .bind(this) es obligatorio: useBus llama el callback como event listener,
        // y en strict mode (ES modules) `this` sería undefined sin el bind.
        useBus(this.busService, "notification", this._onBusNotification.bind(this));

        onMounted(() => this._onMounted());
        onWillUnmount(() => this._onWillUnmount());
    }

    // ========================
    // Lifecycle Methods
    // ========================

    _onMounted() {
        // showBack es true desde el inicio: el botón cancelar se muestra de inmediato.
        this._loadQr();
    }

    _onWillUnmount() {
        this._clearAllTimers();
        this._unsubscribeFromBus();
    }

    _clearAllTimers() {
        if (this._backTimer) {
            clearTimeout(this._backTimer);
            this._backTimer = null;
        }
        if (this._qrTimer) {
            clearInterval(this._qrTimer);
            this._qrTimer = null;
        }
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
            this._statusPollTimer = null;
        }
    }

    // ========================
    // QR Loading
    // ========================

    /**
     * Carga el QR de pago desde el backend.
     */
    async _loadQr() {
        const productTemplate = this.product?.product_tmpl_id || this.product;
        
        if (!productTemplate?.id) {
            this.state.loading = false;
            return;
        }

        try {
            const response = await this._requestQrFromBackend(productTemplate);
            
            if (!response || response.error) {
                if (response?.error_code === 'MACHINE_DISABLED') {
                    this.router.navigate("vending-out-of-service");
                    return;
                }
                this._handleQrError(response?.error);
                return;
            }

            this._processQrResponse(response);
            this._startCountdownTimer();
            
        } catch (error) {
            // console.error("[Vending] Error al cargar QR:", error);
            this._showErrorScreen(ERROR_MESSAGES.CONNECTION_ERROR, ERROR_TITLES.CONNECTION);
        }
    }

    /**
     * Solicita el QR al backend.
     */
    async _requestQrFromBackend(productTemplate) {
        try {
            return await rpc("/v1/vending/qr/create", {
                product_id: productTemplate.id,
                pos_config_id: this.selfOrder?.config?.id,
                description: productTemplate.display_name,
            });
        } catch (rpcError) {
            // console.error("[Vending] Error RPC:", rpcError);
            this._showErrorScreen(ERROR_MESSAGES.CONNECTION_ERROR, ERROR_TITLES.CONNECTION);
            return null;
        }
    }

    /**
     * Maneja errores en la respuesta del QR.
     */
    _handleQrError(errorMessage) {
        const errorTitle = inferErrorTitle(errorMessage);
        this._showErrorScreen(errorMessage || ERROR_MESSAGES.SYSTEM_ERROR, errorTitle);
    }

    /**
     * Procesa respuesta exitosa del QR.
     */
    _processQrResponse(response) {
        const qr = response.qr || {};
        
        // Generar imagen QR
        if (qr.content) {
            this.state.qrUrl = qrCodeSrc(qr.content, { size: 256 });
            this.state.qrContent = qr.content;
        } else {
            this._showErrorScreen(ERROR_MESSAGES.SYSTEM_ERROR, ERROR_TITLES.PAYMENT);
            return;
        }
        
        // Configurar timeout
        const configTimeout = this.selfOrder?.config?.vending_qr_timeout_seconds 
            || VENDING_DEFAULTS.QR_TIMEOUT_SECONDS;
        const qrTimeout = qr.timeout || configTimeout;
        
        this.state.qrTimeout = qrTimeout;
        this.state.qrRemaining = qrTimeout;
        this.state.reference = response.reference || null;
        this.state.loading = false;
        
        // Guardar referencia en selfOrder para otras pantallas
        if (this.state.reference && this.selfOrder) {
            this.selfOrder.vendingReference = this.state.reference;
        }

        // Cargar información del slot seleccionado
        if (response.slot_code) {
            this.state.selectedSlot = {
                code: response.slot_code,
                name: response.slot_name || `Slot ${response.slot_code}`,
            };
        }
        
        // TODO: Eliminar este bloque de console.log antes de ir a producción
        if (this.state.reference) {
            console.log(`%c=== TESTING REFERENCE: ${this.state.reference} ===`, 'background: #222; color: #bada55; font-weight: bold');
            console.log(`PAYMENT APPROVED: ./test_webhook.sh ${this.state.reference} payment-approved --api-key <api-key>`);
            console.log(`DELIVERY SUCCESS: ./test_webhook.sh ${this.state.reference} delivery-success --api-key <api-key>`);
            console.log(`%c--- PAYMENT ERRORS ---`);
            console.log(`./test_webhook.sh ${this.state.reference} payment-rejected --api-key <api-key>`);
            console.log(`%c--- DELIVERY ERRORS ---`);
            console.log(`./test_webhook.sh ${this.state.reference} delivery-error --api-key <api-key>`);
        }
        
        // Suscribirse al bus para notificaciones instantáneas
        if (this.state.reference) {
            this._subscribeToBus();
        }
        
        // Iniciar polling del estado (con intervalo ajustado según bus)
        if (this.state.reference) {
            this._startStatusPolling();
        }
    }

    // ========================
    // Countdown Timer
    // ========================

    /**
     * Inicia el timer de countdown del QR.
     */
    _startCountdownTimer() {
        if (this.state.qrRemaining <= 0) {
            return;
        }

        this._qrTimer = setInterval(() => {
            this._tickCountdown();
        }, 1000);
    }

    /**
     * Procesa cada tick del countdown.
     */
    _tickCountdown() {
        if (this.state.qrRemaining > 0) {
            this.state.qrRemaining -= 1;
        }
        
        // Polling preventivo 2 segundos antes de expirar
        // Útil para capturar pagos de último momento
        if (this.state.qrRemaining === 2) {
            // console.log("[Vending] Polling preventivo antes de expiración");
            this._checkPaymentStatus().catch((error) => {
                // console.warn("[Vending] Error en polling preventivo:", error);
            });
        }
        
        // QR expirado totalmente
        if (this.state.qrRemaining <= 0 && this._qrTimer) {
            clearInterval(this._qrTimer);
            this._qrTimer = null;
            this._showErrorScreen(ERROR_MESSAGES.QR_EXPIRED, ERROR_TITLES.QR_EXPIRED);
        }
    }

    // ========================
    // Bus Notifications
    // ========================

    /**
     * Suscribe al canal del bus para recibir notificaciones de pago.
     * Solo gestiona el canal; el listener se registra vía useBus en setup().
     */
    _subscribeToBus() {
        if (!this.state.reference || !this.busService) {
            // console.warn("[Vending Bus] No se pudo suscribir: referencia o servicio bus no disponible");
            return;
        }

        this._busChannel = `vending_order_${this.state.reference}`;
        this.busService.addChannel(this._busChannel);
        this.state.busActive = true;
        // console.log(`[Vending Bus] Suscrito al canal: ${this._busChannel}`);
    }

    /**
     * Handler de notificaciones del bus.
     * Recibe notificaciones en el formato: { detail: [notifications] }
     */
    _onBusNotification({ detail: notifications }) {
        // console.log("[Vending Bus] Notificación recibida:", notifications);
        if (!notifications || !Array.isArray(notifications)) {
            return;
        }

        // Filtrar notificaciones de nuestro canal
        const relevantNotifications = notifications.filter(
            notif => notif.payload?.channel === this._busChannel
        );

        if (relevantNotifications.length === 0) {
            return;
        }

        // Procesar cada notificación
        relevantNotifications.forEach(notif => {
            const message = notif.payload;
            
            // Verificar tipo de mensaje
            if (message.type !== 'vending_payment_result') {
                return;
            }

            // console.log(`🔔 [Vending Bus] Notificación recibida: ${message.status} - ${message.description}`);
            
            // Procesar actualización instantánea
            this._handleBusUpdate(message);
        });
    }

    /**
     * Procesa actualización recibida vía bus.
     */
    async _handleBusUpdate(message) {
        if (!message || message.reference !== this.state.reference) {
            return;
        }

        const status = message.status;
        const description = message.description || '';

        // console.log(`[Vending Bus] Procesando actualización instantánea: ${status}`);
        this._applyStatusTransition(status, {
            description,
            errorTypeLabel: ERROR_TITLES.DEFAULT,
            source: 'bus',
        });
    }

    /**
     * Elimina el canal del bus al desmontar o cancelar.
     * El listener se remueve automáticamente vía useBus al desmontar el componente.
     */
    _unsubscribeFromBus() {
        if (this._busChannel && this.busService) {
            this.busService.deleteChannel(this._busChannel);
            // console.log(`[Vending Bus] Desuscrito del canal: ${this._busChannel}`);
        }
        this._busChannel = null;
        this.state.busActive = false;
    }

    // ========================
    // Status Polling
    // ========================

    /**
     * Inicia polling del estado de pago.
     * Intervalo ajustado según disponibilidad del bus:
     * - Con bus: 10 segundos (el bus cubre actualizaciones rápidas)
     * - Sin bus: 3 segundos (polling puro)
     */
    _startStatusPolling() {
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
        }

        // Determinar intervalo según disponibilidad del bus
        const pollingInterval = this.state.busActive 
            ? 5000  // 5s con bus (fallback)
            : VENDING_DEFAULTS.POLLING_INTERVAL_MS;  // 3s sin bus

        // console.log(`[Vending Polling] Iniciado con intervalo: ${pollingInterval}ms (bus ${this.state.busActive ? 'activo' : 'inactivo'})`);

        this._statusPollTimer = setInterval(async () => {
            await this._checkPaymentStatus();
        }, pollingInterval);
    }

    /**
     * Consulta el estado actual del pago.
     */
    async _checkPaymentStatus() {
        if (!this.state.reference) {
            return;
        }

        try {
            const response = await rpc("/v1/vending/order/status", {
                reference: this.state.reference,
            });

            if (!response?.found) {
                this._stopPolling();
                return;
            }

            this._handleStatusResponse(response);
            
        } catch (error) {
            // No detener polling en error de red, reintentar
            // console.warn("[Vending] Error consultando estado:", error);
        }
    }

    /**
     * Procesa la respuesta del endpoint de status.
     */
    _handleStatusResponse(response) {
        const status = response.vending_status || response.status;
        this._applyStatusTransition(status, {
            description: response.error_description || '',
            errorTypeLabel: response.error_type_label || ERROR_TITLES.DEFAULT,
            source: 'polling',
        });
    }

    _applyStatusTransition(rawStatus, { description = '', errorTypeLabel = ERROR_TITLES.DEFAULT } = {}) {
        const status = String(rawStatus || '').trim();
        if (!status) {
            return;
        }

        // Entrega tiene prioridad sobre pago para soportar eventos fuera de orden.
        if (DELIVERY_SUCCESS_STATUSES.has(status)) {
            this._stopPolling();
            this._navigateToDeliverySuccess();
            return;
        }

        if (PAYMENT_SUCCESS_STATUSES.has(status)) {
            this._stopPolling();
            this._navigateToPaymentSuccess();
            return;
        }

        if (ERROR_STATUSES.has(status)) {
            this._stopPolling();
            const errorTitle = inferErrorTitle(description) || errorTypeLabel;
            this._showErrorScreen(description || ERROR_MESSAGES.UNKNOWN_ERROR, errorTitle);
            return;
        }

        if (status === 'expired' || status === 'cancelled') {
            this._stopPolling();
        }
    }

    _stopPolling() {
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
            this._statusPollTimer = null;
        }
    }

    // ========================
    // Navigation
    // ========================

    /**
     * Navega a la pantalla de éxito.
     */
    _navigateToDeliverySuccess() {
        if (this.router) {
            this.router.navigate("vending-success");
        }
    }

    _navigateToPaymentSuccess() {
        if (this.router) {
            this.router.navigate("vending-payment-success");
        }
    }

    /**
     * Muestra la pantalla de error.
     */
    _showErrorScreen(errorMessage, errorTitle = ERROR_TITLES.DEFAULT) {
        // Evitar mostrar múltiples errores
        if (this.state.errorShown) {
            return;
        }
        this.state.errorShown = true;

        // Persistir datos para la pantalla de error
        if (this.selfOrder) {
            this.selfOrder.vendingErrorMessage = errorMessage;
            this.selfOrder.vendingErrorTitle = errorTitle;
            this.selfOrder.selectedVendingProduct = this.product;
        }

        // Navegar a pantalla de error
        if (this.router) {
            this.router.navigate("vending-error");
        } else {
            // Fallback: mostrar error en pantalla actual
            this.state.paymentStatus = 'error';
            this.state.error = errorMessage;
        }
    }

    // ========================
    // Backend Calls
    // ========================

    /**
    // ========================
    // User Actions
    // ========================

    /**
     * Handler para botón de regresar/cancelar.
     * Muestra modal de confirmación si ya se generó el QR.
     */
    async regresar() {
        // Si ya se generó el QR, mostrar confirmación
        if (this.state.qrUrl && !this.state.showCancelConfirmation) {
            this.state.showCancelConfirmation = true;
            return;
        }
        
        // Si no hay QR, cancelar directamente
        await this.confirmCancel();
    }

    /**
     * Confirma la cancelación y limpia todo.
     */
    async confirmCancel() {
        // console.log("[Vending] Usuario confirmó cancelación");
        
        // Ocultar modal
        this.state.showCancelConfirmation = false;
        
        // Limpiar timers (countdown, polling)
        this._clearAllTimers();
        
        // CRÍTICO: Desuscribirse del bus ANTES de navegar
        this._unsubscribeFromBus();
        
        // Detener polling
        this._stopPolling();
        
        // Marcar orden como cancelada
        if (this.state.reference) {
            try {
                const orders = await this.orm.searchRead(
                    'pos.order',
                    [['vending_reference', '=', this.state.reference]],
                    ['id']
                );
                
                if (orders.length > 0) {
                    await this.orm.call(
                        'pos.order',
                        'mark_as_user_cancelled',
                        [orders[0].id]
                    );
                }
            } catch (error) {
                // console.warn("[Vending] Error cancelando orden:", error);
            }
        }

        // Navegar
        if (this.props.close) {
            this.props.close();
        } else if (this.router) {
            this.router.back();
        }
    }

    /**
     * Cancela la cancelación (cierra el modal sin hacer nada).
     */
    dismissCancelConfirmation() {
        // console.log("[Vending] Usuario decidió continuar esperando el pago");
        this.state.showCancelConfirmation = false;
    }

    // ========================
    // Computed Getters
    // ========================

    get formattedPrice() {
        return VendingScreenMixin.formatProductPrice(this.product, this.selfOrder);
    }

    get formattedTime() {
        return VendingScreenMixin.formatTime(this.state.qrRemaining);
    }

    get productTemplateId() {
        return VendingScreenMixin.getProductTemplateId(this.product);
    }
}