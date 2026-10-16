/* Dependency-free interaction checks using DOM doubles, not a browser/layout engine.
   Run: node tests/frontend_checks.mjs (Node 20+). */
import assert from 'node:assert/strict';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

class Element {
    constructor(id = '') {
        this.id = id; this.attrs = {}; this.listeners = {}; this.dataset = {};
        this.disabled = false; this.hidden = true; this.type = 'password';
        this.textContent = ''; this.buttons = []; this.method = 'post';
        this.action = 'http://localhost/inventory/categories';
        this.classes = new Set();
        this.classList = {add: value => this.classes.add(value), remove: value => this.classes.delete(value)};
    }
    setAttribute(name,value) { this.attrs[name] = value; }
    removeAttribute(name) { delete this.attrs[name]; }
    addEventListener(type,callback) { (this.listeners[type] ||= []).push(callback); }
    async fire(type,event = {}) { for (const callback of this.listeners[type] || []) await callback(event); }
    querySelectorAll() { return this.buttons; }
    matches() { return !!this.cartForm; }
    closest() { return this.exportLink || null; }
    click() { this.clicked = true; }
    focus() { this.focused = true; }
    before(element) { (this.beforeNodes ||= []).push(element); }
}

function environment({desktop = true, modal = false} = {}) {
    const ids = Object.fromEntries(['operation-status','operation-notice','operation-message','password'].map(id => [id,new Element(id)]));
    if (modal) {
        for (const id of ['confirmation-dialog','confirmation-description','confirmation-cancel','confirmation-accept']) ids[id]=new Element(id);
        ids['confirmation-dialog'].showModal=()=>{ids['confirmation-dialog'].open=true;};
        ids['confirmation-dialog'].close=value=>{ids['confirmation-dialog'].returnValue=value; ids['confirmation-dialog'].open=false; return ids['confirmation-dialog'].fire('close');};
    }
    const toggle = new Element(); toggle.dataset.passwordToggle = 'password';
    const menu = new Element();
    const media = new Element(); media.matches = desktop;
    const document = new Element();
    document.getElementById = id => ids[id] || null;
    document.querySelector = selector => selector === '.workspace-menu' ? menu : null;
    document.querySelectorAll = selector => selector === '[data-password-toggle]' ? [toggle] : [];
    document.created = [];
    document.createElement = () => { const element = new Element(); document.created.push(element); return element; };
    const window = new Element();
    window.matchMedia = () => media;
    window.timers = [];
    window.setTimeout = (callback,delay) => { window.timers.push({callback,delay}); return window.timers.length; };
    window.clearTimeout = () => {};
    window.location = {reload: () => { window.reloaded = true; }};
    return {ids,toggle,menu,media,document,window};
}

export async function runChecks() {
    const names = [];
    const original = {document:globalThis.document, window:globalThis.window, fetch:globalThis.fetch, requestAnimationFrame:globalThis.requestAnimationFrame, FormData:globalThis.FormData, DOMParser:globalThis.DOMParser};
    let sequence = 0;
    const directory = await mkdtemp(join(tmpdir(),'tccp-ui-checks-'));
    const source = await readFile(new URL('../static/js/ui.js',import.meta.url),'utf8');
    async function setup(options) {
        const env = environment(options);
        globalThis.document = env.document;
        globalThis.window = env.window;
        globalThis.requestAnimationFrame = callback => callback();
        const modulePath=join(directory,'ui-'+(++sequence)+'.mjs');
        await writeFile(modulePath,source);
        await import(pathToFileURL(modulePath).href);
        return env;
    }
    async function checkoutEnvironment() {
        const env=await setup();
        const source=await readFile(new URL('../static/js/checkout.js',import.meta.url),'utf8');
        for (const id of ['payment-method','cash-field','cash-received','card-notice','quantity-field']) env.ids[id]=new Element(id);
        env.ids['payment-method'].value='Cash'; env.ids['cash-received'].value='5.00';
        env.document.body={dataset:{uiContext:'owner:store'}};
        env.document.activeElement=env.ids['quantity-field'];
        env.document.layout=new Element();
        env.document.layout.replaceWith=other => { env.document.layout=other; };
        const query=env.document.querySelector;
        env.document.querySelector=selector => selector==='.sale-layout' ? env.document.layout : selector==='.cart-totals' ? {textContent:'Subtotal $2.00 Tax $0.17 Total $2.17'} : query(selector);
        globalThis.FormData=class { constructor(form) {this.form=form; this.values=[];} append(name,value) {this.values.push([name,value]);} };
        const modulePath=join(directory,'checkout-'+(++sequence)+'.mjs');
        await writeFile(modulePath,source); await import(pathToFileURL(modulePath).href);
        return env;
    }
    async function check(name,callback) { await callback(); names.push(name); }
    const submit = form => ({target:form,defaultPrevented:false,preventDefault() {this.defaultPrevented=true;}});
    try {
        await check('Mobile menu collapses and responds to desktop breakpoint', async () => {
            const env=await setup({desktop:false});
            assert.equal(env.menu.open,false);
            env.media.matches=true;
            await env.media.fire('change');
            assert.equal(env.menu.open,true);
        });
        await check('Password visibility toggles without changing value and resets on return', async () => {
            const env=await setup();
            env.ids.password.value='fictional passphrase';
            assert.equal(env.toggle.hidden,false);
            await env.toggle.fire('click');
            assert.equal(env.ids.password.type,'text');
            assert.equal(env.toggle.attrs['aria-pressed'],'true');
            assert.equal(env.ids.password.value,'fictional passphrase');
            await env.window.fire('pageshow',{persisted:false});
            assert.equal(env.ids.password.type,'password');
            assert.equal(env.toggle.attrs['aria-pressed'],'false');
        });
        await check('Native forms announce pending state, block duplicates and recover on pageshow', async () => {
            const env=await setup(); const form=new Element(); const button=new Element(); form.buttons=[button];
            const first=submit(form); await env.document.fire('submit',first);
            assert.equal(first.defaultPrevented,false);
            assert.equal(button.disabled,true);
            assert.equal(form.attrs['aria-busy'],'true');
            assert.match(env.ids['operation-status'].textContent,/Saving/);
            const second=submit(form); await env.document.fire('submit',second);
            assert.equal(second.defaultPrevented,true);
            await env.window.fire('pageshow',{persisted:false});
            assert.equal(button.disabled,false);
            assert.equal(form.attrs['aria-busy'],undefined);
        });
        await check('Canceled confirmations never start loading', async () => {
            const env=await setup(); const form=new Element(); const event=submit(form); event.defaultPrevented=true;
            await env.document.fire('submit',event);
            assert.equal(form.attrs['aria-busy'],undefined);
        });
        await check('Cart requests retain their dedicated submission handler', async () => {
            const env=await setup(); const form=new Element(); form.cartForm=true; form.action='http://localhost/sale/add';
            await env.document.fire('submit',submit(form));
            assert.equal(form.attrs['aria-busy'],undefined);
        });
        await check('Slow native forms restore controls and explain uncertain results', async () => {
            const env=await setup(); const form=new Element(); form.buttons=[new Element()];
            await env.document.fire('submit',submit(form));
            env.window.timers.find(timer=>timer.delay===30000).callback();
            assert.equal(form.buttons[0].disabled,false);
            assert.match(env.ids['operation-message'].textContent,/history before retrying/);
        });
        await check('Back-forward cached private pages reload instead of showing old account state', async () => {
            const env=await setup(); await env.window.fire('pageshow',{persisted:true});
            assert.equal(env.window.reloaded,true);
        });
        await check('Successful CSV download reports real completion and restores link', async () => {
            const env=await setup(); const link=new Element(); link.href='http://localhost/analytics/export/summary.csv'; link.exportLink=link;
            globalThis.fetch=async () => new Response('Metric,Value\r\nGross,1.00\r\n',{headers:{'Content-Type':'text/csv','Content-Disposition':'attachment; filename="tccp-summary.csv"'}});
            const event={target:link,button:0,preventDefault() {this.prevented=true;}};
            await env.document.fire('click',event);
            assert.equal(event.prevented,true);
            assert.equal(link.attrs['aria-busy'],undefined);
            assert.match(env.ids['operation-message'].textContent,/browser has started/);
            assert.equal(env.document.created[0].clicked,true);
            assert.equal(env.document.created[0].download,'tccp-summary.csv');
            // Release the test object URL without a real-minute delay.
            env.window.timers.find(timer=>timer.delay===60000).callback();
        });
        await check('Export errors remain visible and do not claim a download', async () => {
            const env=await setup(); const link=new Element(); link.href='http://localhost/analytics/export/summary.csv'; link.exportLink=link;
            globalThis.fetch=async () => new Response('Unavailable',{status:413,headers:{'Content-Type':'text/html'}});
            await env.document.fire('click',{target:link,button:0,preventDefault(){}});
            assert.equal(env.document.created.length,0);
            assert.match(env.ids['operation-message'].textContent,/Report unavailable/);
            assert.equal(env.ids['operation-notice'].hidden,false);
            assert.equal(env.window.timers.some(timer=>timer.delay===15000),false);
            const dismiss=new Element('dismiss-notice');
            await env.document.fire('click',{target:dismiss});
            assert.equal(env.ids['operation-notice'].hidden,true);
        });
        await check('Modified export clicks retain normal browser behavior', async () => {
            const env=await setup(); const link=new Element(); link.exportLink=link;
            let prevented=false;
            await env.document.fire('click',{target:link,button:0,ctrlKey:true,preventDefault(){prevented=true;}});
            assert.equal(prevented,false);
        });
        await check('Cart network errors restore controls and expose an accessible error', async () => {
            const env=await checkoutEnvironment(); const form=new Element(); form.cartForm=true; form.action='http://localhost/sale/add'; form.buttons=[new Element()];
            globalThis.fetch=async () => {throw new Error('Offline');};
            await env.document.fire('submit',submit(form));
            assert.equal(form.buttons[0].disabled,false);
            assert.equal(form.attrs['aria-busy'],undefined);
            assert.equal(form.beforeNodes[0].attrs.role,'alert');
            assert.match(form.beforeNodes[0].textContent,/verify the cart or transaction history/);
        });
        await check('Cart updates use returned server totals, retain tender and restore focus', async () => {
            const env=await checkoutEnvironment(); const form=new Element(); form.cartForm=true; form.action='http://localhost/sale/update/1'; form.buttons=[new Element()];
            const layout=new Element();
            globalThis.DOMParser=class {parseFromString(){return {body:{dataset:{uiContext:'owner:store'}},querySelector:selector=>selector==='.sale-layout' ? layout : null};}};
            globalThis.fetch=async () => ({ok:true,url:'http://localhost/sale',text:async()=>'<server-rendered-cart>'});
            await env.document.fire('submit',submit(form));
            assert.equal(env.document.layout,layout);
            assert.equal(env.ids['cash-received'].value,'5.00');
            assert.equal(env.ids['quantity-field'].focused,true);
            assert.match(env.ids['operation-status'].textContent,/Total \$2.17/);
            assert.equal(form.buttons[0].disabled,false);
        });
        await check('An account-context change forces full navigation rather than mixing carts', async () => {
            const env=await checkoutEnvironment(); const form=new Element(); form.cartForm=true; form.action='http://localhost/sale/add';
            const initial=env.document.layout;
            env.window.location.assign=value=>{env.window.redirect=value;};
            globalThis.DOMParser=class {parseFromString(){return {body:{dataset:{uiContext:'other:store'}},querySelector:()=>null};}};
            globalThis.fetch=async()=>({ok:true,url:'http://localhost/sale',text:async()=>'<different-account>'});
            await env.document.fire('submit',submit(form));
            assert.equal(env.window.redirect,'http://localhost/sale');
            assert.equal(env.document.layout,initial);
        });
        await check('Shared confirmation cancels without starting a mutation and restores focus', async () => {
            const env=await setup({modal:true}); const form=new Element(); form.dataset.confirm='Deactivate this product?';
            const focus=new Element(); env.document.activeElement=focus;
            const event=submit(form); await env.document.fire('submit',event);
            assert.equal(event.defaultPrevented,true);
            assert.equal(form.attrs['aria-busy'],undefined);
            assert.equal(env.ids['confirmation-dialog'].open,true);
            assert.equal(env.ids['confirmation-description'].textContent,'Deactivate this product?');
            await env.ids['confirmation-cancel'].fire('click');
            assert.equal(env.ids['confirmation-dialog'].open,false);
            assert.equal(focus.focused,true);
            assert.equal(form.attrs['aria-busy'],undefined);
        });
        await check('Shared confirmation submits the approved form once through the pending guard', async () => {
            const env=await setup({modal:true}); const form=new Element(); const button=new Element();
            form.dataset.confirm='Record simulated sale?'; form.buttons=[button];
            let count=0;
            form.requestSubmit=()=>{count++; env.resubmission=env.document.fire('submit',submit(form));};
            await env.document.fire('submit',submit(form));
            await env.ids['confirmation-accept'].fire('click');
            await env.resubmission;
            assert.equal(count,1);
            assert.equal(form.attrs['aria-busy'],'true');
            assert.equal(button.disabled,true);
        });
        await check('Browsers without dialog support retain native confirmation fallback', async () => {
            const env=await setup(); const form=new Element(); form.dataset.confirm='Remove category?';
            env.window.confirm=()=>false;
            const event=submit(form); await env.document.fire('submit',event);
            assert.equal(event.defaultPrevented,true);
            assert.equal(form.attrs['aria-busy'],undefined);
        });
        await check('Browser validation rejection never leaves a reusable confirmation bypass', async () => {
            const env=await setup({modal:true}); const form=new Element(); form.dataset.confirm='Confirm action?';
            form.requestSubmit=()=>{}; // Browser validation blocks dispatching a new submit event.
            await env.document.fire('submit',submit(form));
            await env.ids['confirmation-accept'].fire('click');
            const retry=submit(form); await env.document.fire('submit',retry);
            assert.equal(retry.defaultPrevented,true);
            assert.equal(env.ids['confirmation-dialog'].open,true);
        });
        return {passed:names.length,failed:0,checks:names};
    } finally {
        await rm(directory,{recursive:true,force:true});
        for (const [key,value] of Object.entries(original)) {
            if (value===undefined) delete globalThis[key]; else globalThis[key]=value;
        }
    }
}

if (typeof process !== 'undefined' && process.argv[1] && fileURLToPath(import.meta.url)===process.argv[1]) {
    const result=await runChecks();
    console.log(JSON.stringify(result,null,2));
}
