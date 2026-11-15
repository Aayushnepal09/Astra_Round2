const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const vm = require('node:vm');
const source = readFileSync(__dirname+'/field_suggestions.js','utf8').replace('export class FieldSuggestions','class FieldSuggestions')+'\nglobalThis.Suggestions=FieldSuggestions;';
function element() {
 const classes=new Set();
 return {disabled:false,hidden:false,children:[],style:{},dataset:{},classList:{toggle(k,on){if(on)classes.add(k);else classes.delete(k);},contains(k){return classes.has(k);}},listeners:{},textContent:'',
 addEventListener(k,f){this.listeners[k]=f;},setAttribute(){},replaceChildren(...c){this.children=[];this.append(...c);},
 append(...c){for(const child of c){if(child.parentElement){child.parentElement.children=child.parentElement.children.filter(item=>item!==child);}child.parentElement=this;this.children.push(child);}},focus(){},scrollIntoView(){}};
}
const proposal=()=>({id:'server-proposal',type:'signature',role:'client',label:'Client signature',required:true,page:1,x:.1,y:.3,width:.4,height:.05,evidence:'Signature: ______'});
function fixture(response) {
 const nodes=new Map(),calls=[],timers=[];
 const get=s=>{if(!nodes.has(s))nodes.set(s,element());return nodes.get(s);};
 get('meta[name="csrf-token"]').content='fictional-csrf';
 const context=vm.createContext({document:{querySelector:get,createElement:element},AbortController,Date,
 crypto:{randomUUID:()=> 'new-local-field'},setTimeout:f=>{timers.push(f);return timers.length;},clearTimeout(){},
 fetch:async (...args)=>{calls.push(args);return typeof response==='function'?response():response;}});
 vm.runInContext(source,context);
 const w={agreement:{id:'agreement',pdfSha256:'hash',fieldsRevision:2},companyId:'company',fieldSession:1,pageNumber:1,fields:[],
 publishedView:false,fieldsSaving:false,publishing:false,dirty:false,url:x=>x,checkCompany:r=>assert.equal(r.companyId,'company'),
 markFieldsDirty(){this.dirty=true;},loadPage(p){this.pageNumber=p;},renderFields(){},recoverPreparationSession:async()=>new Error('Sign in again.')};
 const helper=new context.Suggestions(w);helper.update();
 return {helper,w,calls,timers,get};
}
const success=()=>({ok:true,json:async()=>({companyId:'company',agreementId:'agreement',pdfSha256:'hash',revision:2,fields:[proposal()],
 message:'Review before adding.',coverage:{limitedPages:[],selectionLimited:false,selectedPages:[1]}})});
test('requesting suggestions leaves existing fields unsaved and unchanged',async()=>{
 const f=fixture(success());f.w.fields.push({id:'existing'});
 assert.equal(f.calls.length,0);
 await f.helper.request();
 assert.equal(f.w.fields.length,1);assert.equal(f.w.fields[0].id,'existing');assert.equal(f.w.dirty,false);
 assert.equal(f.helper.proposals.length,1);assert.equal(f.calls.length,1);
 assert.match(f.calls[0][0],/field-suggestions/);
});
test('only explicit acceptance adds a local draft field and strips evidence',()=>{
 const f=fixture();const p=proposal();f.helper.proposals=[p];f.helper.add(p);
 assert.equal(f.w.fields.length,1);assert.equal(f.w.fields[0].id,'new-local-field');
 assert.equal('evidence' in f.w.fields[0],false);assert.equal(f.w.dirty,true);
 assert.equal(f.calls.length,0);assert.equal(f.helper.proposals.length,1);assert.equal(f.helper.accepted.get(p),'new-local-field');
});
test('similar existing fields are not duplicated',()=>{
 const f=fixture();const p=proposal();f.w.fields.push({...p,id:'existing'});f.helper.proposals=[p];f.helper.add(p);
 assert.equal(f.w.fields.length,1);assert.equal(f.w.dirty,false);
 assert.match(f.helper.status.textContent,/already in the draft/);
});
test('published views cannot request or add suggested fields',async()=>{
 const f=fixture(success());f.w.publishedView=true;f.helper.update();await f.helper.request();
 const p=proposal();f.helper.proposals=[p];f.helper.add(p);
 assert.equal(f.calls.length,0);assert.equal(f.w.fields.length,0);
});
test('quota failure preserves manual work and never retries automatically',async()=>{
 const f=fixture({ok:false,status:429,headers:{get:()=> '5'},json:async()=>({error:'Wait before retrying.',retryAfter:5})});
 f.w.fields.push({id:'manual'});await f.helper.request();
 assert.equal(f.w.fields.length,1);assert.equal(f.w.dirty,false);assert.equal(f.calls.length,1);
 assert.equal(f.helper.button.disabled,true);f.timers[0]();assert.equal(f.calls.length,1);
 assert.match(f.helper.status.textContent,/existing fields are unchanged/);
});
test('navigation discards a late suggestion response',async()=>{
 let f;f=fixture(()=>{f.w.fieldSession++;return success();});
 await f.helper.request();assert.equal(f.helper.proposals.length,0);assert.equal(f.w.fields.length,0);
});

test('suggestion actions name the destination page and add a field; page navigation preserves the draft',()=>{
 const f=fixture();const p={...proposal(),page:3};f.helper.proposals=[p];f.helper.render();
 const actions=f.helper.list.children[0].children[3];
 assert.equal(actions.children[0].textContent,'Go to page 3');assert.equal(actions.children[1].textContent,'Add field');
 actions.children[0].listeners.click();assert.equal(f.w.pageNumber,3);assert.equal(f.w.dirty,false);assert.equal(f.w.fields.length,0);
});

test('adding keeps the result row visible with a Remove action and never duplicates an existing field',()=>{
 const f=fixture(),p=proposal();f.helper.proposals=[p];f.helper.render();const row=f.helper.list.children[0];
 f.helper.add(p);assert.equal(f.helper.list.children[0],row);assert.equal(f.helper.rows.get(p).add.textContent,'Remove');
 assert.equal(f.helper.rows.get(p).add.disabled,false);assert.match(f.helper.rows.get(p).feedback.textContent,/Added to draft/);
 f.helper.add(p);assert.equal(f.w.fields.length,1);assert.equal(f.calls.length,0);
});

test('existing proposals identify the field already in the draft; fields for a different signer are not silently blocked',()=>{
 const f=fixture(),p=proposal();f.w.fields.push({...p,id:'existing'});f.helper.proposals=[p];f.helper.render();
 assert.equal(f.helper.rows.get(p).add.textContent,'Remove');assert.equal(f.helper.rows.get(p).feedback.textContent,'Already in draft');
 const company={...p,role:'company'};f.helper.proposals=[company];f.helper.render();f.helper.add(company);
 assert.equal(f.w.fields.length,2);assert.equal(f.w.dirty,true);
});

test('Add field visibly waits during saving and becomes available afterwards',()=>{
 const f=fixture(),p=proposal();f.helper.proposals=[p];f.helper.render();f.w.fieldsSaving=true;f.helper.update();
 assert.equal(f.helper.rows.get(p).add.disabled,true);assert.match(f.helper.rows.get(p).feedback.textContent,/Wait/);
 f.helper.add(p);assert.equal(f.w.fields.length,0);f.w.fieldsSaving=false;f.helper.update();assert.equal(f.helper.rows.get(p).add.disabled,false);
});

test('a confirmed save updates the row status without losing the suggestion or adding another field',()=>{
 const f=fixture(),p=proposal();f.helper.proposals=[p];f.helper.render();f.helper.add(p);
 f.w.agreement.fields=[...f.w.fields];f.w.fieldsDirty=false;f.helper.update();
 assert.equal(f.helper.rows.get(p).feedback.textContent,'Saved in draft');assert.match(f.helper.status.textContent,/fields are saved/);
 assert.equal(f.w.fields.length,1);assert.equal(f.helper.proposals.length,1);
});

test('each added suggestion moves after unadded suggestions and earlier additions',()=>{
 const f=fixture(),a=proposal(),b={...proposal(),id:'b',page:2},c={...proposal(),id:'c',page:3};
 f.helper.proposals=[a,b,c];f.helper.render();f.helper.add(c);
 assert.deepEqual(f.helper.list.children,[f.helper.rows.get(a).row,f.helper.rows.get(b).row,f.helper.rows.get(c).row]);
 f.helper.add(a);
 assert.deepEqual(f.helper.list.children,[f.helper.rows.get(b).row,f.helper.rows.get(c).row,f.helper.rows.get(a).row]);
});

test('Remove requires its own confirmation; cancelling preserves the exact field and confirmed removal is local until saved',()=>{
 const f=fixture(),p=proposal();f.helper.proposals=[p];f.helper.render();f.helper.add(p);
 const original=f.w.fields[0];const row=f.helper.rows.get(p);
 f.helper.remove(p);assert.equal(f.w.fields.length,1);
 row.add.listeners.click();assert.equal(row.confirm.hidden,false);assert.equal(f.w.fields[0],original);
 row.confirm.children[2].listeners.click();assert.equal(row.confirm.hidden,true);assert.equal(f.w.fields[0],original);
 row.add.listeners.click();row.confirmButton.listeners.click();
 assert.equal(f.w.fields.length,0);assert.equal(row.add.textContent,'Add field');assert.equal(row.confirm.hidden,true);
 assert.equal(f.w.dirty,true);assert.equal(f.calls.length,0);
});

test('a moved accepted field remains associated with its suggestion for removal',()=>{
 const f=fixture(),p=proposal();f.helper.proposals=[p];f.helper.render();f.helper.add(p);f.w.fields[0].x=.6;
 f.helper.update();assert.equal(f.helper.rows.get(p).add.textContent,'Remove');
 f.helper.askRemove(p);f.helper.remove(p);assert.equal(f.w.fields.length,0);
});

test('saving or publication blocks confirmation and never removes a field from the frozen view',()=>{
 const f=fixture(),p=proposal();f.helper.proposals=[p];f.helper.render();f.helper.add(p);f.helper.askRemove(p);
 for(const flag of ['fieldsSaving','publishing','publishedView']) {f.w[flag]=true;f.helper.remove(p);assert.equal(f.w.fields.length,1);f.w[flag]=false;}
});

