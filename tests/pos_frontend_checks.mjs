/* Executes the actual native designer script against a small DOM model.
   These are interaction tests, not browser rendering or screenshot tests. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

class Node {
    constructor(tag='div') { this.tag=tag; this.children=[]; this.dataset={}; this.attrs={}; this.events={}; this.className=''; this.textContent=''; this.value=''; this.checked=false; this.classList={add:()=>{},remove:()=>{}}; }
    setAttribute(key,value) {this.attrs[key]=value; if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=value;}
    append(...nodes) {for(const node of nodes){if(typeof node==='string')continue;node.remove();node.parentElement=this;this.children.push(node);}}
    replaceChildren(...nodes) {this.children.forEach(node=>node.parentElement=null);this.children=[];this.append(...nodes);}
    remove() {if(this.parentElement){const list=this.parentElement.children;list.splice(list.indexOf(this),1);this.parentElement=null;}}
    insertBefore(node,target) {node.remove();node.parentElement=this;this.children.splice(this.children.indexOf(target),0,node);}
    get previousElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)-1];}
    get nextElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1];}
    matches(selector){return selector.split(',').some(part=>{part=part.trim();if(part==='li')return this.tag==='li';if(part.startsWith('.'))return this.className.split(' ').includes(part.slice(1));if(part.startsWith('['))return Object.hasOwn(this.attrs,part.slice(1,-1));return this.tag===part;});}
    closest(selector){if(selector==='[data-sort-list] li')return this.closest('li');for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
    querySelectorAll(selector){const result=[];for(const node of this.children){if(node.matches(selector))result.push(node);result.push(...node.querySelectorAll(selector));}return result;}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    addEventListener(name,callback){(this.events[name]||=[]).push(callback);}
    async fire(name,target=this,extra={}){for(const callback of this.events[name]||[])await callback({target,preventDefault(){},...extra});}
    focus(){this.focused=true;}
    scrollIntoView(){}
}
function setup(source){
    const document=new Node();const form=new Node('form');form.setAttribute('data-pos-designer','');
    const ids={};const control=(id,tag='input')=>{const node=new Node(tag);ids[id]=node;form.append(node);return node;};
    control('layout-configuration').value=JSON.stringify({preset:'hybrid',density:'comfortable',show_images:true,show_categories:true,product_order:[],category_order:[],favorites:[],colors:{}});
    control('layout-preset','select').value='hybrid';control('layout-density','select').value='comfortable';control('layout-images').checked=true;control('layout-categories').checked=true;
    control('designer-products','ol').setAttribute('data-sort-list','products');control('designer-categories','ol').setAttribute('data-sort-list','categories');control('designer-category','select');
    control('pos-layout-preview');control('pos-preview-categories');
    document.querySelector=selector=>selector==='[data-pos-designer]'?form:null;document.getElementById=id=>ids[id]||null;document.createElement=tag=>new Node(tag);document.createElementNS=(_namespace,tag)=>new Node(tag);
    const window=new Node();window.messages=[];window.TCCPUI={announce:message=>window.messages.push(message)};
    runInNewContext(source,{document,window,Set,JSON,Number,Array,queueMicrotask:callback=>callback()});
    return {document,form,window,ids,config:()=>JSON.parse(ids['layout-configuration'].value)};
}
export async function runChecks(){
    const source=await readFile(new URL('../static/js/pos.js',import.meta.url),'utf8');const names=[];
    const check=async(name,callback)=>{await callback();names.push(name);};
    const pin=async(env,id,name)=>{const button=new Node('button');button.setAttribute('data-pin',String(id));button.dataset.name=name;await env.form.fire('click',button);};
    await check('F2 focuses search and Alt+C opens and focuses the cart',async()=>{
        const env=setup(source);env.ids['sale-search-input']=new Node('input');const cart=new Node();const drawer=new Node('details');cart.append(drawer);env.ids['pos-cart']=cart;
        await env.document.fire('keydown',env.document,{key:'F2'});assert.equal(env.ids['sale-search-input'].focused,true);
        await env.document.fire('keydown',env.document,{key:'c',altKey:true});assert.equal(drawer.open,true);assert.equal(cart.focused,true);
    });
    await check('Pin/unpin persists ordered IDs without copying prices or inventory',async()=>{
        const env=setup(source);await pin(env,1,'<Fictional safe name>');await pin(env,2,'Second product');await pin(env,1,'Duplicate');
        assert.deepEqual(env.config().product_order,[1,2]);assert.equal(env.ids['designer-products'].children[0].querySelector('li'),null);
        assert.equal(env.ids['pos-layout-preview'].children[0].children[1].textContent,'<Fictional safe name>');
        const row=env.ids['designer-products'].children[0];await env.form.fire('click',row.querySelector('[data-unpin]'));assert.deepEqual(env.config().product_order,[2]);
        assert.equal(Object.hasOwn(env.config(),'prices'),false);
    });
    await check('Keyboard/touch move buttons reorder and preserve focus',async()=>{
        const env=setup(source);await pin(env,1,'One');await pin(env,2,'Two');const move=env.ids['designer-products'].children[1].querySelectorAll('[data-move]')[0];
        await env.form.fire('click',move);assert.deepEqual(env.config().product_order,[2,1]);assert.equal(move.focused,true);
    });
    await check('Native drag/drop reorders within its own list',async()=>{
        const env=setup(source);await pin(env,1,'One');await pin(env,2,'Two');const list=env.ids['designer-products'];const [first,second]=list.children;
        await list.fire('dragstart',second,{dataTransfer:{setData(){}}});await list.fire('drop',first);await list.fire('dragend');assert.deepEqual(env.config().product_order,[2,1]);
    });
    await check('Favorites, tile color and density are serialized as presentation choices',async()=>{
        const env=setup(source);await pin(env,1,'One');const row=env.ids['designer-products'].children[0];const favorite=row.querySelector('[data-favorite]');favorite.checked=true;await env.form.fire('change',favorite);
        row.querySelector('[data-color]').value='purple';env.ids['layout-preset'].value='quick';env.ids['layout-density'].value='large';await env.form.fire('change',env.ids['layout-density']);
        assert.deepEqual(env.config().favorites,[1]);assert.equal(env.config().colors['1'],'purple');assert.match(env.ids['pos-layout-preview'].className,/density-large/);
    });
    await check('Product fallback uses an SVG and category preview follows visibility',async()=>{
        const env=setup(source);await pin(env,1,'Fictional product');
        assert.equal(env.ids['pos-layout-preview'].querySelectorAll('svg').length,1);
        env.ids['layout-categories'].checked=false;
        await env.form.fire('change',env.ids['layout-categories']);
        assert.equal(env.ids['pos-preview-categories'].hidden,true);
        assert.equal(env.config().show_categories,false);
    });
    await check('Canceled save retains dirty state; preview contains no checkout forms',async()=>{
        const env=setup(source);await pin(env,1,'One');await env.form.fire('submit',env.form,{defaultPrevented:true});const event={preventDefault(){this.prevented=true;}};
        for(const callback of env.window.events.beforeunload)callback(event);assert.equal(event.prevented,true);assert.equal(env.ids['pos-layout-preview'].querySelectorAll('form').length,0);
    });
    return {passed:names.length,failed:0,checks:names};
}
if(typeof process!=='undefined'&&process.argv[1]?.endsWith('pos_frontend_checks.mjs'))console.log(JSON.stringify(await runChecks(),null,2));
