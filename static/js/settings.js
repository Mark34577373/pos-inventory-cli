'use strict';
(() => {
    const dirty = new Set();
    document.querySelectorAll('[data-settings-form]').forEach(form => {
        form.addEventListener('input', () => dirty.add(form));
        form.addEventListener('change', () => dirty.add(form));
        form.addEventListener('submit', event => {
            // The shared confirmation handler cancels the first submission.
            queueMicrotask(() => { if (!event.defaultPrevented) dirty.delete(form); });
        });
    });
    window.addEventListener('beforeunload', event => {
        if (dirty.size) { event.preventDefault(); event.returnValue = ''; }
    });
})();
