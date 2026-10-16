/* Totals always come back from Flask; no client-side financial arithmetic. */
(() => {
    let busy = false;
    function paymentFields() {
        const method = document.getElementById('payment-method');
        if (!method) return;
        const cash = method.value === 'Cash';
        document.getElementById('cash-field').hidden = !cash;
        document.getElementById('cash-received').required = cash;
        document.getElementById('card-notice').hidden = cash;
    }
    document.addEventListener('change', event => {
        if (event.target.id === 'payment-method') paymentFields();
        if (event.target.matches('.cart-quantity-form input[name="quantity"]')) {
            event.target.form.requestSubmit();
        }
    });
    document.addEventListener('submit', async event => {
        const form = event.target;
        const cartForm = form.matches('.sale-layout form[method="post"]');
        if (!cartForm && !form.hasAttribute('data-once')) return;
        if (busy) { event.preventDefault(); return; }
        // Cancellation retains its normal browser confirmation and navigation.
        if (form.action.endsWith('/sale/cancel')) return;
        if (!cartForm) {
            busy = true;
            form.querySelectorAll('button[type="submit"]').forEach(button => button.disabled = true);
            return;
        }
        event.preventDefault();
        const focusId = document.activeElement?.id;
        const body = new FormData(form);
        if (event.submitter?.name) body.append(event.submitter.name, event.submitter.value);
        busy = true;
        form.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(form.action, {method: 'POST', body, credentials: 'same-origin'});
            if (!response.ok) {
                const errorPage = new DOMParser().parseFromString(await response.text(), 'text/html');
                const errorContent = errorPage.querySelector('.page-content');
                if (errorContent) document.querySelector('.page-content').replaceWith(errorContent);
                else throw new Error('Server unavailable');
                return;
            }
            if (new URL(response.url).pathname !== '/sale') {
                window.location.assign(response.url);
                return;
            }
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            const layout = page.querySelector('.sale-layout');
            if (!layout) { window.location.assign(response.url); return; }
            document.querySelector('.sale-layout').replaceWith(layout);
            document.querySelector('.flash-stack')?.remove();
            const messages = page.querySelector('.flash-stack');
            if (messages) document.querySelector('.sale-layout').before(messages);
            paymentFields();
            if (focusId) document.getElementById(focusId)?.focus();
        } catch (_) {
            const message = document.createElement('p');
            message.setAttribute('role', 'alert');
            message.textContent = 'Connection interrupted. Reload to verify the cart or transaction history before retrying.';
            form.before(message);
        } finally { busy = false; form.removeAttribute('aria-busy'); }
    });
    window.addEventListener('pageshow', () => {
        busy = false;
        document.querySelectorAll('[data-once] button[type="submit"]').forEach(button => button.disabled = false);
    });
    paymentFields();
})();
