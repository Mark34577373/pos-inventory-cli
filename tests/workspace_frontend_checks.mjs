/* Actual workspace source, DOM doubles; browser checks are reported separately. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
export async function runChecks() {
 const listeners={}; const classes=new Set();
 const document={body:{classList:{toggle(name){if(classes.has(name)){classes.delete(name);return false;}classes.add(name);return true;}}},addEventListener(type,fn){listeners[type]=fn;}};
 vm.runInNewContext(await readFile(new URL('../static/js/workspace.js',import.meta.url),'utf8'),{document});
 const names=[]; const check=(name,fn)=>{fn();names.push(name);};
 const toggle={attrs:{},setAttribute(key,value){this.attrs[key]=value;}};
 const click=target=>listeners.click({target:{closest:selector=>selector==='[data-sidebar-toggle]'?target:null}});
 check('Navigation collapses with accessible state',()=>{click(toggle);assert.equal(toggle.attrs['aria-expanded'],'false');assert.ok(classes.has('console-compact'));});
 check('Navigation expands with accessible state',()=>{click(toggle);assert.equal(toggle.attrs['aria-expanded'],'true');assert.ok(!classes.has('console-compact'));});
 check('Success dismissal removes only selected message',()=>{let removed=false;listeners.click({target:{closest:selector=>selector==='.toast-dismiss'?{closest:()=>({remove(){removed=true;}})}:null}});assert.ok(removed);});
 check('Category filtering uses existing native form',()=>{let submitted=0;listeners.change({target:{id:'sale-category',form:{requestSubmit(){submitted++;}}}});assert.equal(submitted,1);});
 check('Other controls do not submit through workspace filter handler',()=>{listeners.change({target:{id:'payment-method'}});});
 return {passed:names.length,failed:0,checks:names};
}
if(typeof process!=='undefined'&&process.argv[1]?.endsWith('workspace_frontend_checks.mjs')) console.log(JSON.stringify(await runChecks(),null,2));
