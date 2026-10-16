/* Demo enhancement only; every amount and checkout decision comes from the server. */
(() => {
    const method = document.getElementById('demo-payment-method');
    const cash = document.getElementById('demo-cash');
    const result = document.getElementById('demo-change');
    const preview = document.getElementById('demo-preview');
    if (!method || !cash || !preview) return;
    let generation = 0;
    function sync() {
        generation++;
        const isCash = method.value === 'Cash';
        document.getElementById('demo-cash-fields').hidden = !isCash;
        cash.required = isCash;
        if (!isCash) cash.value = '';
        result.textContent = '';
    }
    method.addEventListener('change', sync);
    cash.addEventListener('input', () => { generation++; result.textContent = ''; });
    sync();
    preview.addEventListener('click', async () => {
        const revision = ++generation;
        preview.disabled = true;
        result.textContent = 'Calculating fictional change…';
        const data = new URLSearchParams({cash_received: cash.value,
            csrf_token: preview.form.querySelector('[name="csrf_token"]').value});
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(preview.dataset.url, {method: 'POST', body: data,
                headers: {Accept: 'application/json'}, credentials: 'same-origin', signal: controller.signal});
            const value = await response.json();
            if (generation === revision) result.textContent = response.ok ? `Fictional change: ${value.change}` : (value.error || 'Unable to preview change.');
        } catch (_) {
            if (generation === revision) result.textContent = 'Unable to preview change. Check the connection and try again.';
        } finally { clearTimeout(timeout); preview.disabled = false; }
    });
})();
