const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
function node() {
  return { children:[], dataset:{}, style:{}, hidden:false, listeners:{},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    addEventListener(k,f){this.listeners[k]=f;},setAttribute(){},removeAttribute(){},decode:async()=>{},
    scrollIntoView(options){this.scrolled=true;this.scrollOptions=options;},remove(){},getBoundingClientRect:()=>({top:0,bottom:800}),add(child){this.append(child);},querySelector:()=>({}),
  };
}
function fixture(geometry = [{width:792,height:612}]) {
  const pending=[],revoked=[],errors=[];
  const context=vm.createContext({document:{createElement:node},window:{addEventListener(){},removeEventListener(){}},
    IntersectionObserver:class{observe(){}disconnect(){}},AbortController,innerHeight:800,requestAnimationFrame:()=>1,cancelAnimationFrame(){},
    URL:{createObjectURL:()=> 'blob:'+Math.random(),revokeObjectURL:x=>revoked.push(x)},
    fetch:(url,options)=>new Promise((resolve,reject)=>{pending.push({url,options,resolve});options.signal.addEventListener('abort',()=>{const error=new Error();error.name='AbortError';reject(error);});})});
  vm.runInContext(readFileSync(__dirname+'/shared/scroll_document.js','utf8').replace('export class ScrollDocument','class ScrollDocument')+'\nglobalThis.Viewer=ScrollDocument;',context);
  const root=node();const viewer=new context.Viewer({root,count:6,geometry,url:n=>'/page/'+n,onError:e=>errors.push(e)});
  const finish=index=>pending[index].resolve({ok:true,blob:async()=>({type:'image/png'})});
  return {viewer,root,pending,revoked,errors,finish};
}
test('continuous pages retain geometry, limit concurrent loads, and release distant images without dropping page placeholders',async()=>{
  const f=fixture();assert.equal(f.root.children.length,6);assert.equal(f.viewer.pages.get(1).canvas.style.aspectRatio,'792 / 612');
  f.viewer.ensure(2);f.viewer.ensure(3);assert.equal(f.pending.length,2);
  f.finish(0);await tick();assert.equal(f.pending.length,3);f.finish(1);f.finish(2);await tick();
  f.viewer.goTo(6);assert.equal(f.revoked.length,3);assert.equal(f.root.children.length,6);assert.equal(f.viewer.pages.get(6).section.scrolled,true);
  f.finish(3);await tick();f.viewer.destroy();assert.equal(f.root.children.length,0);assert.equal(f.revoked.length,4);
});
test('rotated PDF pages use their displayed proportions before images load',()=>{
  const f=fixture([0,90,180,270].map(rotation=>({width:612,height:792,rotation})));
  assert.deepEqual([1,2,3,4].map(number=>f.viewer.pages.get(number).canvas.style.aspectRatio),
    ['612 / 792','792 / 612','612 / 792','792 / 612']);
  f.viewer.destroy();
});
test('jumping away aborts an obsolete page request without surfacing an error',async()=>{
  const f=fixture();f.viewer.goTo(5);assert.equal(f.pending[0].options.signal.aborted,true);
  await tick();f.finish(1);await tick();assert.equal(f.errors.length,0);assert.equal(f.viewer.pages.get(5).image.hidden,false);f.viewer.destroy();
});
test('page authentication failures are passed to the signing access boundary',async()=>{
  const f=fixture();f.pending[0].resolve({ok:false,status:401,json:async()=>({error:'Verify access again.'})});await tick();
  assert.equal(f.errors[0].status,401);assert.equal(f.viewer.pages.get(1).retry.hidden,false);f.viewer.destroy();
});

test('reading the bottom of a page keeps that page active instead of selecting the next page heading',()=>{
 const f=fixture();f.viewer.pages.forEach((page,index)=>{page.canvas.getBoundingClientRect=()=>({top:1000*index,bottom:1000*index+900});});
 f.viewer.pages.get(1).canvas.getBoundingClientRect=()=>({top:-500,bottom:600});
 f.viewer.pages.get(2).canvas.getBoundingClientRect=()=>({top:630,bottom:1730});
 assert.equal(f.viewer.visiblePage(),1);f.viewer.destroy();
});

test('adding a field reveals its exact position, including before its page image finishes loading',()=>{
 const f=fixture();f.viewer.revealField({page:6,x:.1,y:.85,width:.3,height:.05});
 assert.equal(f.viewer.current,6);assert.equal(f.viewer.fieldTarget.style.top,'85%');
 assert.equal(f.viewer.fieldTarget.scrollOptions.block,'center');assert.equal(f.viewer.pages.get(6).image.hidden,true);f.viewer.destroy();
});
test('company preparation renders and selects fields on every page',()=>{
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
  const context=vm.createContext({document:{activeElement:null,createElement:node},Option:function(text,value){return {text,value};}});
  const source=readFileSync(__dirname+'/agreements.js','utf8').replace(/^import .*;\r?\n/gm,'').replace('export class AgreementWorkspace','class AgreementWorkspace');
  vm.runInContext(source+'\nglobalThis.Workspace=AgreementWorkspace;',context);
  const w=Object.create(context.Workspace.prototype);w.element=get;w.pageNumber=1;w.agreement={pageCount:2};
  w.viewer={pages:new Map([[1,{fields:node(),image:{hidden:false}}],[2,{fields:node(),image:{hidden:false}}]])};
  w.fields=[1,2].map(page=>({id:'field'+page,page,label:'Name',role:'client',type:'name',x:.1,y:.1,width:.3,height:.05}));
  w.renderFields();assert.equal(w.viewer.pages.get(1).fields.children.length,1);assert.equal(w.viewer.pages.get(2).fields.children.length,1);
  w.viewer.pages.get(2).fields.children[0].listeners.click({stopPropagation(){}});
  assert.equal(w.pageNumber,2);assert.equal(w.selectedFieldId,'field2');assert.equal(w.viewer.pages.get(2).fields.children.length,1);
});
