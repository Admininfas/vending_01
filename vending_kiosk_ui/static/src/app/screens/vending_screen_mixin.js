/** @odoo-module **/

/**
 * Mixin con funcionalidad compartida entre las pantallas de vending.
 * Proporciona lógica común para countdown, navegación y formateo de precios.
 * 
 * @module vending_kiosk_ui/screens/vending_screen_mixin
 */

/**
 * Constantes de configuración por defecto para las pantallas de vending.
 * Estas se usan como fallback cuando no hay configuración del servidor.
 */
export const VENDING_DEFAULTS = {
    /** Segundos antes de volver automáticamente al menú */
    COUNTDOWN_SECONDS: 40,
    /** Segundos de timeout para el QR */
    QR_TIMEOUT_SECONDS: 120,
    /** Intervalo de polling en milisegundos */
    POLLING_INTERVAL_MS: 3000,
    /** Segundos antes de mostrar el botón de volver */
    SHOW_BACK_DELAY_MS: 5000,
};

/**
 * Mensajes de error por defecto (en español para la UI).
 */
export const ERROR_MESSAGES = {
    UNKNOWN_ERROR: "Ocurrió un error desconocido",
    GENERIC_HELP: "Por favor, intenta nuevamente o contacta con el administrador.",
    QR_EXPIRED: "El tiempo para pagar ha finalizado. Por favor, vuelva a intentarlo.",
    CONNECTION_ERROR: "No se pudo conectar con el sistema de pagos. Por favor, intente más tarde.",
    SYSTEM_ERROR: "No se pudo completar la operación. Por favor, intente nuevamente.",
};

/**
 * Títulos de error según tipo.
 */
export const ERROR_TITLES = {
    DEFAULT: "Ocurrió un Error",
    NO_STOCK: "Ocurrió un Error",
    PRODUCT_UNAVAILABLE: "Ocurrió un Error",
    CONFIGURATION: "Ocurrió un Error",
    PAYMENT: "Ocurrió un Error",
    SYSTEM: "Ocurrió un Error",
    QR_EXPIRED: "QR Expirado",
    CONNECTION: "Ocurrió un Error",
};

/**
 * Infiere el título de error apropiado basándose en el mensaje de error.
 * 
 * @param {string} errorMessage - Mensaje de error a analizar
 * @returns {string} Título apropiado para mostrar
 */
export function inferErrorTitle(errorMessage) {
    return ERROR_TITLES.DEFAULT;
}

/**
 * Mixin con métodos utilitarios para pantallas de vending.
 * Se usa aplicándolo al prototype del componente.
 * 
 * @example
 * // En el setup() del componente:
 * Object.assign(this, VendingScreenMixin);
 */
export const VendingScreenMixin = {
    /**
     * Obtiene los segundos de countdown configurados o el valor por defecto.
     * 
     * @returns {number} Segundos de countdown
     */
    getCountdownSeconds() {
        return this.selfOrder?.config?.vending_countdown_seconds || VENDING_DEFAULTS.COUNTDOWN_SECONDS;
    },

    /**
     * Formatea el precio del producto actual para mostrar.
     * 
     * @param {Object} product - Producto a formatear
     * @param {Object} selfOrder - Servicio de self order
     * @returns {string} Precio formateado o cadena vacía
     */
    formatProductPrice(product, selfOrder) {
        if (!product || !selfOrder) {
            return "";
        }
        const productTemplate = product.product_tmpl_id || product;
        const price = selfOrder.getProductDisplayPrice(productTemplate, product);
        return selfOrder.formatMonetary
            ? selfOrder.formatMonetary(price)
            : String(price);
    },

    /**
     * Obtiene el ID del product.template para usar en URLs de imagen.
     * 
     * @param {Object} product - Producto
     * @returns {number|null} ID del template o null
     */
    getProductTemplateId(product) {
        if (!product) {
            return null;
        }
        return product.product_tmpl_id?.id || product.id;
    },

    /**
     * Formatea segundos a formato mm:ss.
     * 
     * @param {number} totalSeconds - Total de segundos
     * @returns {string} Tiempo formateado (ej: "2:05")
     */
    formatTime(totalSeconds) {
        const remaining = totalSeconds || 0;
        const minutes = Math.floor(remaining / 60);
        const seconds = remaining % 60;
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    },

    /**
     * Limpia un timer de intervalo de forma segura.
     * 
     * @param {string} timerName - Nombre de la propiedad del timer en this
     */
    clearTimer(timerName) {
        if (this[timerName]) {
            clearInterval(this[timerName]);
            this[timerName] = null;
        }
    },

    /**
     * Limpia un timeout de forma segura.
     * 
     * @param {string} timerName - Nombre de la propiedad del timer en this
     */
    clearTimeoutSafe(timerName) {
        if (this[timerName]) {
            clearTimeout(this[timerName]);
            this[timerName] = null;
        }
    },

    /**
     * Navega al menú principal de forma segura.
     * Intenta usar close callback primero, luego router.
     * 
     * @param {Function|null} closeCallback - Función de cierre opcional
     * @param {Object|null} router - Router de navegación
     */
    navigateToMenu(closeCallback, router) {
        if (closeCallback) {
            closeCallback();
            return;
        }
        if (router) {
            router.navigate("default");
        }
    },
};

export default VendingScreenMixin;
