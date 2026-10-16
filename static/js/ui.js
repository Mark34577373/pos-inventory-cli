/* Shared progressive enhancement. No account data, prices or tokens are persisted. */
(() => {
    const status = () => document.getElementById('operation-status');
    const notice = () => document.getElementById('operation-notice');
    const pendingForms = new Map();
    let downloadController = null;
    const announce = message => { if (status()) status().textContent = message; };
    const showNotice = message => {
        const target = notice();
        if (target) { document.getElementById('operation-message').textContent = message; target.hidden = false; }
        announce(message);
    };
    const begin = (form, message) => {
        if (pendingForms.has(form)) return false;
        const buttons = [...form.querySelectorAll('button[type="submit"],button:not([type]),input[type="submit"]')];
        pendingForms.set(form, buttons.map(button => [button, button.disabled]));
        form.setAttribute('aria-busy', 'true');
        announce(message);
        showNotice(message);
        return true;
    };
    const disable = form => {
        (pendingForms.get(form) || []).forEach(([button]) => {
            button.disabled = true;
            button.classList.add('loading-label');
        });
    };
    const finish = (form, message = '') => {
        (pendingForms.get(form) || []).forEach(([button, disabled]) => {
            button.disabled = disabled;
            button.classList.remove('loading-label');
        });
        pendingForms.delete(form);
        form.removeAttribute('aria-busy');
        if (notice()) notice().hidden = true;
        if (message) announce(message);
    };
    window.TCCPUI = {begin, disable, finish, announce};
    const menu = document.querySelector('.workspace-menu');
    if (menu) {
        const desktop = window.matchMedia('(min-width: 1024px)');
        const sync = () => { menu.open = desktop.matches; };
        sync();
        desktop.addEventListener('change', sync);
    }
    document.querySelectorAll('[data-password-toggle]').forEach(button => {
        button.hidden = false;
        button.addEventListener('click', () => {
            const input = document.getElementById(button.dataset.passwordToggle);
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            button.textContent = show ? 'Hide' : 'Show';
            button.setAttribute('aria-pressed', String(show));
        });
    });
    const approvedForms = new WeakSet();
    const dialog = document.getElementById('confirmation-dialog');
    let pendingConfirmation = null;
    const resume = ({form, submitter}) => {
        approvedForms.add(form);
        try { form.requestSubmit(submitter && !submitter.disabled ? submitter : undefined); }
        finally { approvedForms.delete(form); }
    };
    document.addEventListener('submit', event => {
        const form = event.target;
        if (approvedForms.has(form)) { approvedForms.delete(form); return; }
        if (event.defaultPrevented || !form.dataset.confirm) return;
        event.preventDefault();
        if (pendingConfirmation) return;
        const request = {form, submitter: event.submitter, focus: document.activeElement};
        if (!dialog || typeof dialog.showModal !== 'function') {
            if (window.confirm(form.dataset.confirm)) resume(request);
            return;
        }
        pendingConfirmation = request;
        document.getElementById('confirmation-description').textContent = form.dataset.confirm;
        dialog.returnValue = '';
        dialog.showModal();
    }, true);
    if (dialog) {
        document.getElementById('confirmation-cancel').addEventListener('click', () => dialog.close('cancel'));
        document.getElementById('confirmation-accept').addEventListener('click', () => dialog.close('confirm'));
        dialog.addEventListener('close', () => {
            const request = pendingConfirmation;
            pendingConfirmation = null;
            if (!request) return;
            request.focus?.focus();
            if (dialog.returnValue === 'confirm') resume(request);
        });
    }
    document.addEventListener('submit', event => {
        const form = event.target;
        if (event.defaultPrevented || (form.matches('.sale-layout form[method="post"]') && !form.action.endsWith('/sale/cancel'))) return;
        if (!begin(form, form.dataset.pendingLabel || (form.method === 'post' ? 'Saving your request…' : 'Loading results…'))) {
            event.preventDefault(); return;
        }
        // The browser first captures the successful submitter name/value.
        requestAnimationFrame(() => disable(form));
        // Native navigations may be interrupted; recover rather than spin forever.
        window.setTimeout(() => {
            if (pendingForms.has(form)) {
                finish(form);
                showNotice('Still waiting? Check your connection. Check stock or transaction history before retrying.');
            }
        }, 30000);
    });
    document.addEventListener('click', async event => {
        if (event.target.id === 'dismiss-notice') { if (notice()) notice().hidden = true; return; }
        const link = event.target.closest('a[data-export]');
        if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (downloadController) { announce('A report download is already pending.'); return; }
        downloadController = new AbortController();
        const controller = downloadController;
        const timer = window.setTimeout(() => controller.abort(), 45000);
        let succeeded = false;
        link.setAttribute('aria-busy', 'true');
        link.setAttribute('aria-disabled', 'true');
        showNotice('Preparing your report download…');
        try {
            const response = await fetch(link.href, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
            if (!response.ok || !response.headers.get('Content-Type')?.includes('text/csv')) {
                throw new Error('Report unavailable. Review the selected filters and sign-in status, then try again.');
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            const filename = response.headers.get('Content-Disposition')?.match(/filename="([a-zA-Z0-9._-]+)"/);
            anchor.download = filename ? filename[1] : 'tccp-report.csv';
            anchor.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 60000);
            succeeded = true;
            showNotice('Report received. Your browser has started the download.');
        } catch (error) {
            showNotice(error.name === 'AbortError' ? 'Download timed out or was canceled. Narrow the date range and retry.' : error.message);
        } finally {
            window.clearTimeout(timer);
            downloadController = null;
            link.removeAttribute('aria-busy');
            link.removeAttribute('aria-disabled');
            if (succeeded) window.setTimeout(() => { if (!downloadController && notice()) notice().hidden = true; }, 15000);
        }
    });
    window.addEventListener('pagehide', () => downloadController?.abort());
    window.addEventListener('pageshow', event => {
        if (event.persisted) { window.location.reload(); return; }
        [...pendingForms.keys()].forEach(form => finish(form));
        const error = document.querySelector('.error-summary,.flash-error[role="alert"]');
        if (error) { error.setAttribute('tabindex', '-1'); error.focus(); }
        document.querySelectorAll('[data-password-toggle]').forEach(button => {
            document.getElementById(button.dataset.passwordToggle).type = 'password';
            button.textContent = 'Show'; button.setAttribute('aria-pressed', 'false');
        });
    });
})();
