const assert = require('node:assert/strict');
const test = require('node:test');
const {initializeColumnWidths, readColumnWidths, COLUMN_WIDTHS_KEY} = require('../static/modules/records/column_widths.mjs');

function node() {
  const events=new Map(),classes=new Set();
  return {style:{},dataset:{},attributes:{},children:[],hidden:false,
    classList:{add:n=>classes.add(n),remove:n=>classes.delete(n),contains:n=>classes.has(n),toggle:(n,on)=>on?classes.add(n):classes.delete(n)},
    setAttribute(k,v){this.attributes[k]=v;},append(child){this.children.push(child);},
    addEventListener(k,callback){if(!events.has(k))events.set(k,[]);events.get(k).push(callback);},
    fire(type,props={}){for(const fn of events.get(type)||[])fn({button:0,pointerId:1,clientX:0,preventDefault(){},stopPropagation(){},...props});},
    setPointerCapture(id){this.capture=id;},hasPointerCapture(id){return this.capture===id;},releasePointerCapture(){this.capture=null;}};
}
function harness(storage=new Map()) {
  const nodes=new Map(),$=selector=>{if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);};
  const defaults=[34,280,298,100,322,104,102],cols=defaults.map(()=>node()),headers=defaults.map((width,i)=>({...node(),getBoundingClientRect:()=>({width:parseFloat(cols[i].style.width)||width})}));
  $('#records-table').querySelectorAll=selector=>selector==='col'?cols:headers;
  const document={...node(),body:node(),createElement:node},window=node();
  const localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
  initializeColumnWidths({environment:{document,window,localStorage},ui:{$}});
  return {$,cols,handles:headers.map(h=>h.children[0]),document,window,storage};
}

test('locked controls ignore input; a pointer drag changes only its column and persists on release',()=>{
  const h=harness(),handle=h.handles[1];
  handle.fire('pointerdown');handle.fire('pointermove',{clientX:80});handle.fire('pointerup');
  assert.equal(h.storage.size,0);assert(h.handles.every(x=>x.hidden));
  h.$('#records-column-lock').fire('click');
  handle.fire('pointerdown',{clientX:100});handle.fire('pointermove',{clientX:180});
  assert.equal(h.storage.size,0);
  handle.fire('pointerup');
  const saved=JSON.parse(h.storage.get(COLUMN_WIDTHS_KEY));
  assert.deepEqual(saved.widths,[34,360,298,100,322,104,102]);
  assert.equal(h.$('#records-table').style.width,'1320px');
  h.$('#records-column-lock').fire('click');handle.fire('keydown',{key:'ArrowRight'});
  assert.equal(h.cols[1].style.width,'360px');assert(h.handles.every(x=>x.hidden));
});

test('reload restores widths but starts locked; reset removes the preference and returns to fluid defaults',()=>{
  const storage=new Map([[COLUMN_WIDTHS_KEY,JSON.stringify({version:1,widths:[40,400,270,100,300,100,120]})]]);
  const h=harness(storage);
  assert.equal(h.cols[1].style.width,'400px');assert(h.handles.every(x=>x.hidden));
  h.$('#records-column-reset').fire('click');assert.equal(storage.size,1);
  h.$('#records-column-lock').fire('click');h.$('#records-column-reset').fire('click');
  assert.equal(storage.size,0);assert(h.cols.every(c=>c.style.width===''));
  assert.equal(h.$('#records-table').style.width,'');assert.equal(h.$('#records-table').style.minWidth,'');
});

test('cancelled gestures and Escape restore the original layout; keyboard respects column limits',()=>{
  const h=harness(),handle=h.handles[2];h.$('#records-column-lock').fire('click');
  for(const cancel of ['pointercancel','Escape']) {
    handle.fire('pointerdown');handle.fire('pointermove',{clientX:-1000});
    assert.equal(h.cols[2].style.width,'170px');
    if(cancel==='Escape')h.document.fire('keydown',{key:'Escape'});else handle.fire(cancel);
    assert.equal(h.cols[2].style.width,'');assert.equal(h.storage.size,0);
    assert(!h.document.body.classList.contains('records-column-resizing'));
  }
  for(let i=0;i<30;i++)handle.fire('keydown',{key:'ArrowRight',shiftKey:true});
  assert.equal(h.cols[2].style.width,'900px');
  h.window.fire('hashchange');assert(h.handles.every(x=>x.hidden));
});

test('invalid or unavailable storage falls back to defaults without breaking the page',()=>{
  for(const value of ['bad json','null',JSON.stringify({version:1,widths:[400]}),JSON.stringify({version:1,widths:[0,280,298,100,322,104,102]}),JSON.stringify({version:2,widths:[34,280,298,100,322,104,102]})]) {
    assert.equal(readColumnWidths({getItem:()=>value}),null);
  }
  assert.equal(readColumnWidths({getItem(){throw Error('unavailable');}}),null);
});
