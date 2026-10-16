/* Execute the actual demo enhancement; these are DOM-model tests, not visual QA. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

class Element {
    constructor() { this.value = ''; this.listeners = {}; this.textContent = ''; }
    addEventListener(type, callback) { this.listeners[type] = callback; }
    fire(type) { return this.listeners[type]?.(); }
}

export async function runChecks() {
    const source = await readFile(new URL('../static/js/demo.js', import.meta.url), 'utf8');
    function setup(fetch) {
        const ids = Object.fromEntries(['demo-payment-method','demo-cash','demo-change','demo-preview','demo-cash-fields'].map(key => [key, new Element()]));
        ids['demo-payment-method'].value = 'Cash';
        ids['demo-preview'].dataset = {url:'/demo/cash-preview'};
        ids['demo-preview'].form = {querySelector: () => ({value:'fictional-csrf'})};
        runInNewContext(source, {document:{getElementById:key=>ids[key]}, fetch, URLSearchParams, AbortController, setTimeout, clearTimeout});
        return ids;
    }
    const names = [];
    async function check(name, callback) { await callback(); names.push(name); }
    await check('Card simulation clears tender and never collects card credentials', async () => {
        const ids = setup();
        assert.equal(ids['demo-cash'].required, true);
        ids['demo-cash'].value = '10';
        ids['demo-payment-method'].value = 'Card';
        ids['demo-payment-method'].fire('change');
        assert.equal(ids['demo-cash'].value, '');
        assert.equal(ids['demo-cash'].required, false);
        assert.equal(ids['demo-cash-fields'].hidden, true);
    });
    await check('Cash preview submits CSRF and displays the authoritative formatted change', async () => {
        let request;
        const ids = setup(async (url, options) => { request = {url, options}; return {ok:true, json:async()=>({change:'$1.72'})}; });
        ids['demo-cash'].value = '10.00';
        await ids['demo-preview'].fire('click');
        assert.equal(request.url, '/demo/cash-preview');
        assert.equal(request.options.body.get('csrf_token'), 'fictional-csrf');
        assert.equal(request.options.body.get('cash_received'), '10.00');
        assert.equal(request.options.credentials, 'same-origin');
        assert.equal(ids['demo-change'].textContent, 'Fictional change: $1.72');
        assert.equal(ids['demo-preview'].disabled, false);
    });
    await check('Late preview replies cannot replace feedback after the amount changes', async () => {
        let release;
        const ids = setup(() => new Promise(resolve => { release = resolve; }));
        const pending = ids['demo-preview'].fire('click');
        ids['demo-cash'].fire('input');
        release({ok:true,json:async()=>({change:'$9,999.00'})});
        await pending;
        assert.equal(ids['demo-change'].textContent, '');
    });
    await check('Server and network errors remain visible and restore the preview button', async () => {
        for (const fetch of [async()=>({ok:false,json:async()=>({error:'Insufficient fictional cash.'})}), async()=>{throw new Error('network');}]) {
            const ids = setup(fetch);
            await ids['demo-preview'].fire('click');
            assert.match(ids['demo-change'].textContent, /Insufficient fictional cash|Unable to preview change/);
            assert.equal(ids['demo-preview'].disabled, false);
        }
    });
    return {passed:names.length, failed:0, checks:names};
}
