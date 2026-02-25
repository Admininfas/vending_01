/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ProductListPage } from "@pos_self_order/app/pages/product_list_page/product_list_page";
import { useVendingProductBus } from "../../hooks/use_vending_product_bus";
import { useState } from "@odoo/owl";

/**
 * Patch para ProductListPage que agrega soporte para modo vending.
 * 
 * En modo vending:
 * - Filtra productos para mostrar solo los que tienen stock en la máquina
 * - Redirige directamente a la pantalla de procesamiento de pago
 * - Se suscribe al bus para recibir actualizaciones de productos en tiempo real
 * - Muestra información de slots disponibles en las cards de productos
 */
patch(ProductListPage.prototype, {
    setup() {
        super.setup();
        
        // Estado local para forzar re-render cuando cambian productos
        this.vendingState = useState({
            lastUpdate: Date.now(),
        });
        
        this._initVendingMode();
        this._initVendingProductBus();
    },

    /**
     * Inicializa el modo vending si está activo.
     */
    _initVendingMode() {
        if (!this._isVendingMode()) {
            return;
        }

        if (this.selfOrder.config._vending_no_machine) {
            this.vendingError = "No hay máquina expendedora configurada para este punto de venta";
        }
    },

    /**
     * Inicializa la suscripción al bus para actualizaciones de productos.
     */
    _initVendingProductBus() {
        if (!this._isVendingMode()) {
            return;
        }

        // Usar el hook para suscribirse al bus
        useVendingProductBus(this.selfOrder, (updatedProducts) => {
            // Callback cuando se actualicen los productos
            console.log(`[Vending] Productos actualizados, forzando re-render...`);
            
            // Actualizar timestamp para forzar re-render
            this.vendingState.lastUpdate = Date.now();
        });
    },

    /**
     * Verifica si el POS está en modo vending.
     */
    _isVendingMode() {
        return this.selfOrder.config.self_ordering_mode === 'vending';
    },

    /**
     * Filtra productos para modo vending.
     * Solo muestra productos que tienen stock en los slots de la máquina.
     */
    _filterVendingProducts(products) {
        const availableProductIds = this.selfOrder.config._vending_available_products || [];
        
        if (!availableProductIds.length) {
            return [];
        }
        
        return products.filter(product => availableProductIds.includes(product.id));
    },

    getProducts(category) {
        if (!this._isVendingMode()) {
            return super.getProducts(category);
        }

        if (this.vendingError) {
            return [];
        }

        const products = super.getProducts(category);
        const filteredProducts = this._filterVendingProducts(products);
        
        // Agregar información de slots a cada producto
        const productSlots = this.selfOrder.config._vending_product_slots || {};
        for (const product of filteredProducts) {
            product._vending_slots = productSlots[product.id] || [];
        }
        
        return filteredProducts;
    },

    selectProduct(product, target) {
        if (!this._isVendingMode()) {
            return super.selectProduct(product, target);
        }

        // Guardar producto y navegar a procesamiento
        this.selfOrder.selectedVendingProduct = product;
        this.router.navigate("vending-process");
    }
});