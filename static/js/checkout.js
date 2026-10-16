/* Totals always come back from Flask; no client-side financial arithmetic. */
(() => {
    let busy = false;
    let uncertain = false;
    const context = document.body?.dataset.uiContext;
    const pendingKey = 'tccp:pending-checkout:' + context;
    const readPending = () => { try { return window.sessionStorage?.getItem(pendingKey); } catch (_) { return null; } };
    const remember = token => { try { window.sessionStorage?.setItem(pendingKey, token); } catch (_) { /* Same-token server protection remains active. */ } };
    const forget = () => { try { window.sessionStorage?.removeItem(pendingKey); } catch (_) { /* No persistent browser storage. */ } };
    function recovery(form, token, csrf) {
        uncertain = true; busy = true;
        const paymentPanel = form.closest('.payment-panel');
        if (paymentPanel) paymentPanel.open = true;
        const message = document.createElement('p'); message.setAttribute('role', 'alert');
        message.textContent = 'Checkout status is uncertain. Do not start a replacement sale. Check the recorded result before retrying.';
        const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary-button'; button.textContent = 'Check checkout status';
        form.before(message); form.before(button);
        form.querySelectorAll('button[type="submit"]').forEach(control => control.disabled = true);
        button.addEventListener('click', async () => {
            button.disabled = true;
            const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), 15000);
            try {
                const body = new URLSearchParams({csrf_token: csrf, checkout_token: token});
                const response = await fetch('/api/pos/checkout-status', {method: 'POST', credentials: 'same-origin', body, signal: controller.signal});
                if (!response.ok || response.redirected) throw new Error('Status unavailable');
                const result = await response.json();
                if (result.context !== context) { window.location.assign('/sale'); return; }
                if (result.state === 'completed') { forget(); window.location.assign(result.receipt_url); return; }
                if (result.state !== 'not_found' || result.retry !== 'same_token_only') throw new Error('Status uncertain');
                forget(); uncertain = false; busy = false;
                message.textContent = 'No completed sale was found. Retry this original checkout confirmation; its token prevents duplicate recording.';
                form.querySelectorAll('button[type="submit"]').forEach(control => control.disabled = false);
                button.hidden = true;
            } catch (_) { message.textContent = 'The result is still uncertain. Keep this sale open and check status again; do not create a replacement sale.'; }
            finally { button.disabled = false; window.clearTimeout(timeout); }
        });
    }
    function paymentFields() {
        const method = document.getElementById('payment-method');
        if (!method) return;
        const cash = method.value === 'Cash';
        document.getElementById('cash-field').hidden = !cash;
        document.getElementById('cash-received').required = cash;
        document.getElementById('card-notice').hidden = cash;
    }
    document.addEventListener('click', async event => {
        const button = event.target.closest('[data-cash-preview]');
        if (!button || busy) return;
        const output = document.getElementById('cash-change-preview');
        const form = button.closest('form');
        const tender = document.getElementById('cash-received').value;
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 15000);
        button.disabled = true; output.textContent = 'Checking current total and change…';
        try {
            const body = new URLSearchParams({csrf_token: new FormData(form).get('csrf_token'), cash_received: tender});
            const response = await fetch('/api/pos/cash-quote', {method: 'POST', credentials: 'same-origin', body, signal: controller.signal});
            const result = await response.json();
            if (document.getElementById('cash-received').value !== tender) { output.textContent = 'Cash amount changed. Preview again.'; return; }
            if (!response.ok) { output.textContent = result.error || 'Review the cash amount and cart availability.'; return; }
            if (result.context !== context) { window.location.assign('/sale'); return; }
            output.textContent = 'Current total ' + result.total_text + ' · Change due ' + result.change_text + '. Rechecked again at checkout.';
        } catch (_) { output.textContent = 'Change preview unavailable. Check your connection and retry; no sale was recorded by this preview.'; }
        finally { button.disabled = false; window.clearTimeout(timeout); }
    });
    document.addEventListener('input', event => {
        if (event.target.id === 'cash-received') { const output = document.getElementById('cash-change-preview'); if (output) output.textContent = ''; }
    });
    document.addEventListener('change', event => {
        if (event.target.id === 'payment-method') paymentFields();
        if (event.target.matches('.cart-quantity-form input[name="quantity"]')) {
            event.target.form.requestSubmit();
        }
    });
    document.addEventListener('submit', async event => {
        const form = event.target;
        const cartForm = form.matches('.sale-layout form[method="post"]');
        if (!cartForm || event.defaultPrevented) return;
        if (busy) { event.preventDefault(); return; }
        // Cancellation retains its normal browser confirmation and navigation.
        if (form.action.endsWith('/sale/cancel')) return;
        event.preventDefault();
        const focusId = document.activeElement?.id;
        const selectedMethod = document.getElementById('payment-method')?.value;
        const tender = document.getElementById('cash-received')?.value;
        const body = new FormData(form);
        if (event.submitter?.name) body.append(event.submitter.name, event.submitter.value);
        const checkout = form.action.endsWith('/sale/checkout');
        if (checkout) remember(body.get('checkout_token'));
        busy = true;
        window.TCCPUI?.begin(form, form.action.endsWith('/sale/checkout') ? 'Recording simulated sale…' : 'Updating your cart…');
        window.TCCPUI?.disable(form);
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 30000);
        form.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(form.action, {method: 'POST', body, credentials: 'same-origin', signal: controller.signal});
            if (!response.ok) {
                if (checkout) throw new Error('Checkout result needs verification');
                const errorPage = new DOMParser().parseFromString(await response.text(), 'text/html');
                const errorContent = errorPage.querySelector('.page-content');
                if (errorContent) { document.querySelector('.page-content').replaceWith(errorContent); const error = document.querySelector('.error-summary'); error?.focus(); }
                else throw new Error('Server unavailable');
                return;
            }
            if (new URL(response.url).pathname !== '/sale') {
                if (checkout && new URL(response.url).pathname.includes('receipt')) forget();
                window.location.assign(response.url);
                return;
            }
            if (checkout) forget();
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            if (page.body.dataset.uiContext !== document.body.dataset.uiContext) { window.location.assign(response.url); return; }
            const layout = page.querySelector('.sale-layout');
            if (!layout) { window.location.assign(response.url); return; }
            document.querySelector('.sale-layout').replaceWith(layout);
            document.querySelector('.flash-stack')?.remove();
            const messages = page.querySelector('.flash-stack');
            if (messages) document.querySelector('.sale-layout').before(messages);
            const method = document.getElementById('payment-method');
            const cashInput = document.getElementById('cash-received');
            if (method && selectedMethod) method.value = selectedMethod;
            if (cashInput && tender) cashInput.value = tender;
            paymentFields();
            if (focusId) document.getElementById(focusId)?.focus();
            window.TCCPUI?.announce(document.querySelector('.cart-totals')?.textContent || 'Cart updated.');
        } catch (_) {
            if (checkout) { uncertain = true; }
            else {
                const message = document.createElement('p');
                message.setAttribute('role', 'alert');
                message.textContent = 'Connection interrupted. Reload to verify the cart or transaction history before retrying.';
                form.before(message);
            }
        } finally {
            busy = uncertain; window.clearTimeout(timer); window.TCCPUI?.finish(form); form.removeAttribute('aria-busy');
            if (checkout && uncertain) recovery(form, body.get('checkout_token'), body.get('csrf_token'));
        }
    });
    window.addEventListener('pageshow', () => {
        busy = uncertain;
        document.querySelectorAll('[data-once] button[type="submit"]').forEach(button => button.disabled = false);
    });
    paymentFields();
    const pending = readPending();
    if (pending) {
        const target = document.querySelector('.checkout-form') || document.querySelector('.sale-layout');
        const csrf = document.querySelector('input[name="csrf_token"]')?.value;
        if (target && csrf) recovery(target, pending, csrf);
    }
})();
