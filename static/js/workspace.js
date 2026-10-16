/* Native workspace enhancements. No prices, credentials, or customer data are stored. */
(() => {
    document.addEventListener('click', event => {
        const dismiss = event.target.closest('.toast-dismiss');
        if (dismiss) dismiss.closest('.flash-message')?.remove();
        const toggle = event.target.closest('[data-sidebar-toggle]');
        if (toggle) {
            const compact = document.body.classList.toggle('console-compact');
            toggle.setAttribute('aria-expanded', String(!compact));
            toggle.textContent = compact ? '⇥ Expand' : '⇤ Compact navigation';
        }
    });
    // Success feedback stays until dismissed. Critical errors stay inline.
    // Search and category selection use native GET forms and their existing backend.
    document.addEventListener('change', event => {
        if (event.target.id === 'sale-category') event.target.form.requestSubmit();
    });
})();
