const assert = require('node:assert/strict');
const test = require('node:test');
const {createImports} = require('../static/modules/imports.mjs');
const ReceiptImport = require('../static/import_files.js');

function harness({legacy = false, failUpload = false, failSetup = false, groupSize = 5000} = {}) {
  const nodes = new Map(), requests = [], notices = [], startStates = [];
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {value:'', disabled:false, textContent:'', close(){},
      classList:{add(){},remove(){},toggle(){}}, addEventListener(){}});
    return nodes.get(selector);
  };
  const importsState = {batchRunning:false,ocrBackend:'vision',sealRecognitionMode:'qingtong'}, progressState = {};
  let taskNumber = 0;
  const plan = {fields:['danzhengtong'],date:['danzhengtong'],seal:['qingtong','danzhengtong']};
  const controller = createImports({
    environment:{document:{},window:{confirm(){throw Error('unexpected confirmation');}},location:{hash:''},
      localStorage:{setItem(){}}, FormData:class {constructor(){this.values=[];} append(...entry){this.values.push(entry);}}},
    ui:{$,$$:()=>[],toast:message=>notices.push(message)}, importsState, progressState, recordsState:{},
    ReceiptImport:{...ReceiptImport,batches:items=>Array.from({length:Math.ceil(items.length/groupSize)},
      (_,index)=>items.slice(index*groupSize,(index+1)*groupSize))},
    readRecognitionPlan:()=>plan, usesRemotePlan:()=>true,updateRecognitionPlan(){
      if(failSetup) { failSetup=false; throw Error('UI setup failed'); }
    },
    syncStartRecognition:()=>startStates.push({uploading:importsState.batchRunning,scanning:importsState.importScanning}),
    loadDailyResults:async()=>{},
    api:async(url,options)=>{
      requests.push({url,options});
      if(url==='/api/tasks') {
        const n=++taskNumber;
        return {id:`task-${n}`,created_at:'2026-09-11T10:00:00+08:00',
          items:options.json.items.map((item,i)=>({id:`job-${n}-${i}`,filename:item.filename,
            ...(!legacy ? {start_requested:false,status:'awaiting_upload'} : {})}))};
      }
      assert.match(url,/^\/api\/jobs\/job-\d-\d\/upload$/);
      if(failUpload) throw Error('upload interrupted');
      return {status:'ready',start_requested:false};
    },
  });
  return {controller,importsState,progressState,requests,notices,startStates,$,plan};
}

test('remote-config import uploads locally without a popup or starting recognition',async()=>{
  const h=harness();
  await h.controller.runBatch([{name:'receipt.jpg'}],[],'导入回单');
  assert.deepEqual(h.requests.map(request=>request.url),['/api/tasks','/api/jobs/job-1-0/upload']);
  assert.deepEqual(h.requests[0].options.json.recognition_config,h.plan);
  assert.equal(h.progressState.workFilter,'processing');
  assert.equal(h.importsState.batchRunning,false);
  assert.equal(h.importsState.uploadingJobs.size,0);
  assert(h.startStates.some(state=>state.uploading));
  assert.equal(h.startStates.at(-1).uploading,false);
  assert.match(h.notices.at(-1),/已导入 1 张图片.*点击“开始识别”/);
});

test('an old auto-start backend receives no image bytes',async()=>{
  const h=harness({legacy:true});
  await assert.rejects(h.controller.runBatch([{name:'receipt.jpg'}],[],'导入回单'),/尚未支持“导入后开始”/);
  assert.deepEqual(h.requests.map(request=>request.url),['/api/tasks']);
  assert.equal(h.importsState.batchRunning,false);
  assert.equal(h.$('#choose-file').disabled,false);
});

test('all import groups stay staged and none starts automatically',async()=>{
  const h=harness({groupSize:2});
  await h.controller.runBatch([{name:'1.jpg'},{name:'2.jpg'},{name:'3.jpg'}],[],'文件夹');
  assert.equal(h.requests.filter(request=>request.url==='/api/tasks').length,2);
  assert.equal(h.requests.filter(request=>request.url.endsWith('/upload')).length,3);
  assert(!h.requests.some(request=>request.url.endsWith('/start')));
  assert.match(h.notices.at(-1),/已导入 3 张/);
});

test('upload failure leaves a clear pending-start message and resets the controls',async()=>{
  const h=harness({failUpload:true});
  await h.controller.runBatch([{name:'receipt.jpg'}],[],'导入回单');
  assert.match(h.notices.at(-1),/1 张图片的上传未确认.*等待你点击“开始识别”/);
  assert.equal(h.importsState.batchRunning,false);
  assert.equal(h.$('#choose-folder').disabled,false);
  assert(!h.requests.some(request=>request.url.endsWith('/start')));
});

test('a UI setup failure releases import controls before any upload',async()=>{
  const h=harness({failSetup:true});
  await assert.rejects(h.controller.runBatch([{name:'receipt.jpg'}],[],'导入回单'),/UI setup failed/);
  assert.deepEqual(h.requests,[]);
  assert.equal(h.importsState.batchRunning,false);
  assert.equal(h.importsState.uploadingJobs.size,0);
  assert.equal(h.$('#choose-file').disabled,false);
  assert.equal(h.$('#choose-folder').disabled,false);
  assert.equal(h.startStates.at(-1).uploading,false);
});
