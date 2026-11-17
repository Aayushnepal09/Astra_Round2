const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const source = readFileSync(__dirname+'/client_agreement.js','utf8').replace(/^import .*;\r?\n/gm,'')
  .replace('new AgreementReview();','globalThis.Controller = SigningController;');

function fixture() {
  const nodes = new Map(), all = [], calls = [], visited = [];
  const create = () => {
    const node = { value:'', checked:false, hidden:false, disabled:false, children:[], listeners:{}, dataset:{}, style:{}, attributes:{},
      addEventListener(k,f){this.listeners[k]=f;}, setAttribute(k,v){this.attributes[k]=v;}, removeAttribute(k){delete this.attributes[k];},
      append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}},
      prepend(child){child.parentElement=this;this.children.unshift(child);}, add(child){this.append(child);},
      replaceChildren(...children){this.children=[];this.append(...children);},
      closest(selector){let item=this;while(item){if(selector==='.field'&&item.className==='field')return item;if(selector==='.signing-form'&&item===get('#signing-form'))return item;item=item.parentElement;}return get(selector);},
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},getBoundingClientRect(){return {height:500};},
      focus(){this.focused=true;}, scrollIntoView(){}, showModal(){this.open=true;}, close(){this.open=false;},
    };all.push(node);return node;
  };
  const get = selector => {if(!nodes.has(selector))nodes.set(selector,create());return nodes.get(selector);};
  get('#signing-save').parentElement=create();
  const document={querySelector:get,createElement:create,getElementById:id=>all.find(n=>n.id===id)||get('#'+id),body:{dataset:{signingUrl:'/fictional/sign/'}}};
  const context=vm.createContext({document,window:{addEventListener(){},removeEventListener(){}},Option:function(text,value){return {text,value};},crypto:{randomUUID:()=> 'fictional-operation'}});
  vm.runInContext(source,context);
  const definitions=[['sig1','signature','Client signature'],['sig2','signature','Second signature'],['name','name','Printed name'],
    ['email','text','Email'],['other','name','Guarantor name'],['date','date','Date'],['ack','acknowledgement','I agree']];
  const state={requestId:'r',templateVersionId:'v',recipientId:'p',pdfSha256:'h',revision:0,values:{},signer:{name:'Fictional Signer',email:'signer@example.test'},
    fields:definitions.map(([id,type,label],index)=>({id,type,label,required:true,page:index<3?1:2,x:.1,y:.1,width:.3,height:.05})),consentText:'Fictional test consent only.'};
  const review={client:{request:async(...args)=>{calls.push(args);return {signing:state};}},load:p=>visited.push(p),clearImage(){},refresh(){}};
  const controller=new context.Controller(review,state);
  return {controller,get,calls,visited};
}

test('signature adoption and later taps fill only the selected field without sending or checking consent',()=>{
  const f=fixture(),c=f.controller;
  c.activateField('sig1',{place:true});
  assert.equal(f.get('#signature-dialog').open,true);
  assert.equal(c.inputs.get('sig1').value,'');
  c.adoptSignature({preventDefault(){}});
  assert.equal(c.inputs.get('sig1').value,'Fictional Signer');
  assert.equal(c.inputs.get('sig2').value,'');
  c.activateField('sig2',{place:true});
  assert.equal(c.inputs.get('sig2').value,'Fictional Signer');
  assert.equal(c.consent.checked,false);assert.equal(f.calls.length,0);
});

test('autofill matches empty name/email fields, preserving other people, signatures, dates, consent and existing values',()=>{
  const f=fixture(),c=f.controller;
  c.inputs.get('email').value='custom@example.test';c.consent.checked=true;c.autofill();
  assert.equal(c.inputs.get('name').value,'Fictional Signer');
  assert.equal(c.inputs.get('email').value,'custom@example.test');
  for(const id of ['other','sig1','sig2','date'])assert.equal(c.inputs.get(id).value,'');
  assert.equal(c.inputs.get('ack').checked,false);assert.equal(c.consent.checked,false);assert.equal(f.calls.length,0);
  c.inputs.get('email').value='';c.autofill();assert.equal(c.inputs.get('email').value,'signer@example.test');
});

test('expired, uncertain or recorded requests cannot autofill or apply signatures',()=>{
  for(const locked of ['expired','uncertain','receipt']){
    const f=fixture(),c=f.controller;if(locked==='receipt')c.state.receipt={id:'recorded'};else c[locked]=true;
    c.autofill();c.activateField('sig1',{place:true});c.adoptSignature({preventDefault(){}});
    assert.equal(c.inputs.get('name').value,'');assert.equal(c.inputs.get('sig1').value,'');assert.equal(f.calls.length,0);
  }
});

test('field selection and validation reveal only the requested editor and retain other entries',()=>{
  const f=fixture(),c=f.controller;c.inputs.get('name').value='Keep this entry';
  c.activateField('date',{navigate:true});assert.deepEqual(f.visited,[2]);
  assert.equal(c.inputs.get('date').closest('.field').hidden,false);
  assert.equal(c.inputs.get('name').closest('.field').hidden,true);
  c.showError({message:'Required name',fieldErrors:{name:['Enter a name.']}});
  assert.equal(c.inputs.get('name').closest('.field').hidden,false);
  assert.equal(c.inputs.get('name').value,'Keep this entry');
});

test('signing still requires consent; editing a signature clears its adopted value',async()=>{
  const f=fixture(),c=f.controller;
  await c.send('submit');assert.equal(f.calls.length,0);assert.match(c.error.textContent,/intent to sign/);
  c.adoptedSignature='Fictional Signer';
  c.form.listeners.input({target:c.inputs.get('sig1')});
  assert.equal(c.adoptedSignature,null);assert.equal(c.consent.checked,false);
});

test('Next required field visits all unfinished fields, skips filled fields, and wraps without overwriting values',()=>{
 const f=fixture(),c=f.controller;
 c.nextIncomplete();assert.equal(c.activeFieldId,'sig1');c.nextIncomplete();assert.equal(c.activeFieldId,'sig2');
 c.inputs.get('name').value='Existing name';c.nextIncomplete();assert.equal(c.activeFieldId,'email');
 c.nextIncomplete();assert.equal(c.activeFieldId,'other');c.nextIncomplete();assert.equal(c.activeFieldId,'date');
 c.nextIncomplete();assert.equal(c.activeFieldId,'ack');c.nextIncomplete();assert.equal(c.activeFieldId,'sig1');
 assert.equal(c.inputs.get('name').value,'Existing name');assert.equal(c.inputs.get('sig1').value,'');assert.equal(f.calls.length,0);
});

test('Next required field handles a single remaining field and then directs the signer to consent',()=>{
 const f=fixture(),c=f.controller;
 c.state.fields.forEach(field=>{const input=c.inputs.get(field.id);if(field.type==='acknowledgement')input.checked=true;else input.value='filled';});
 c.inputs.get('sig2').value='';c.nextIncomplete();assert.equal(c.activeFieldId,'sig2');
 c.inputs.get('sig2').value='Fictional Signer';c.nextIncomplete();assert.equal(c.consent.focused,true);
 assert.equal(c.consent.checked,false);assert.equal(f.calls.length,0);
});

test('loading adjacent pages and changing values keep existing marker buttons and their click handlers intact',()=>{
 const f=fixture(),c=f.controller;
 const page=()=>({fields:{children:[],append(child){child.parentElement=this;this.children.push(child);},replaceChildren(){this.children=[];}},image:{hidden:false}});
 c.review.viewer={pages:new Map([[1,page()],[2,page()]])};c.renderMarkers();
 const first=c.markers.get('sig1'),handler=first.listeners.click;
 c.inputs.get('name').value='Keep name';c.renderMarkers();
 assert.equal(c.markers.get('sig1'),first);assert.equal(first.listeners.click,handler);
 assert.equal(c.review.viewer.pages.get(1).fields.children.length,3);
 first.listeners.click();assert.equal(f.get('#signature-dialog').open,true);assert.equal(c.activeFieldId,'sig1');
});

test('direct box taps and Next navigation reveal the exact field rather than just its page',()=>{
 const f=fixture(),c=f.controller,revealed=[];c.review.reveal=field=>revealed.push(field.id);
 c.activateField('date',{place:true});assert.equal(c.activeFieldId,'date');assert.deepEqual(revealed,['date']);
 c.nextIncomplete();assert.equal(c.activeFieldId,'ack');assert.deepEqual(revealed,['date','ack']);
});
