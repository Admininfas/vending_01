/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ProductListPage } from "@pos_self_order/app/pages/product_list_page/product_list_page";
import { useVendingProductBus } from "../../hooks/use_vending_product_bus";

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
        
        this.vendingBus = null;
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
            return;
        }

        if (this.selfOrder.config._vending_machine_fault_blocked) {
            this.vendingError = "La máquina expendedora está desactivada por falla.";
            this.router.navigate("vending-out-of-service");
        }
    },

    /**
     * Inicializa la suscripción al bus para actualizaciones de productos.
     */
    _initVendingProductBus() {
        if (!this._isVendingMode()) {
            return;
        }

        this.vendingBus = useVendingProductBus(this.selfOrder, (snapshot) => {
            const machineBlocked = Boolean(snapshot?.machineFaultBlocked);

            if (machineBlocked) {
                this.vendingError = "La máquina expendedora está desactivada por falla.";
                this.router.navigate("vending-out-of-service");
                return;
            }

            this.vendingError = null;
        });
    },

    _isMachineFaultBlocked() {
        return Boolean(
            this.vendingBus?.vendingProducts?.machineFaultBlocked
            || this.selfOrder?.config?._vending_machine_fault_blocked
        );
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
        const availableProductIds = this.vendingBus?.vendingProducts?.availableIds || [];
        
        if (!availableProductIds.length) {
            return [];
        }
        
        return products.filter(product => availableProductIds.includes(product.id));
    },

    _getProductMinSlotCodeMap() {
        return (
            this.vendingBus?.vendingProducts?.productMinSlotCode ||
            this.selfOrder?.config?._vending_product_min_slot_code ||
            {}
        );
    },

    _sortVendingProducts(products) {
        const minSlotCodeByProduct = this._getProductMinSlotCodeMap();
        return [...products].sort((productA, productB) => {
            const rawCodeA = Number(minSlotCodeByProduct[productA.id]);
            const rawCodeB = Number(minSlotCodeByProduct[productB.id]);
            const codeA = Number.isFinite(rawCodeA) ? rawCodeA : Number.MAX_SAFE_INTEGER;
            const codeB = Number.isFinite(rawCodeB) ? rawCodeB : Number.MAX_SAFE_INTEGER;

            if (codeA !== codeB) {
                return codeA - codeB;
            }

            const nameA = String(productA.display_name || productA.name || '').toLocaleLowerCase();
            const nameB = String(productB.display_name || productB.name || '').toLocaleLowerCase();
            const byName = nameA.localeCompare(nameB, undefined, { sensitivity: 'base' });
            if (byName !== 0) {
                return byName;
            }

            return (productA.id || 0) - (productB.id || 0);
        });
    },

    getProducts(category) {
        if (!this._isVendingMode()) {
            return super.getProducts(category);
        }

        if (this.vendingError) {
            return [];
        }

        if (this._isMachineFaultBlocked()) {
            return [];
        }

        const products = super.getProducts(category);
        const filteredProducts = this._filterVendingProducts(products);
        const sortedProducts = this._sortVendingProducts(filteredProducts);
        
        // Agregar información de slots a cada producto
        const productSlots = this.vendingBus?.vendingProducts?.productSlots || {};
        for (const product of sortedProducts) {
            product._vending_slots = productSlots[product.id] || [];
        }
        
        return sortedProducts;
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