const assert = require('node:assert/strict');
const test = require('node:test');
const {execFileSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const {renderRunHistory, runDuration, callImageUrl} = require('../static/modules/review/run_history_view.mjs');
const {createReviewRunHistory} = require('../static/modules/review/run_history.mjs');
const {resolveRunTimeline,timelineSummary,renderRunTimeline} = require('../static/modules/review/run_timeline.mjs');

const time = second => `2026-10-26T14:32:${String(second).padStart(2,'0')}+08:00`;
const call = (id, extra = {}) => ({id,stage:'seal',method:'paddle_v6',method_label:'Paddle OCR',
  action:'印章文字识别',input_name:`seal_${id}.png`,width:640,height:180,seconds:2,
  status:'succeeded',started_at:time(id),...extra});
const response = (calls = [call(1),call(2),call(3,{seconds:3,status:'failed',error:'识别超时'})], extra = {}) => ({
  filename:'customer/receipt.jpg',available:true,status:'succeeded',
  summary:{total:3,succeeded:2,failed:1,cache_hits:4},calls,
  started_at:time(0),finished_at:time(5),processing_seconds:5,model_initialization_seconds:0,result_id:9,...extra});
const imageProcessing = () => ({seconds:8,steps:[
  {stage:'seal',operation:'rotation',label:'旋转校正',calls:20,seconds:3,failed:0},
  {stage:'date',operation:'crop',label:'裁剪',calls:4,seconds:.5,failed:0},
  {stage:'seal',operation:'unwrap',label:'文字展开',calls:2,seconds:4.5,failed:1},
]});
const timelineSpan = (id,lane,start,end,extra={}) => ({id,label:id,lane,started_at:time(start),finished_at:time(end),
  seconds:end-start,kind:'recognition',status:'succeeded',...extra});
const timeline = (extra={}) => ({version:1,started_at:time(0),finished_at:time(10),elapsed_seconds:10,
  approximate:false,runs:[{id:'current',kind:'current',label:'本次处理',complete:true,elapsed_seconds:10,
    started_at:time(0),finished_at:time(10),spans:[timelineSpan('整页读取','local_page',0,6),
      timelineSpan('日期读取','date',3,7),timelineSpan('印章读取','seal',3,9),
      timelineSpan('单证通历时','danzhengtong',0,10,{kind:'lifecycle'}),
      timelineSpan('上传','danzhengtong',0,1,{kind:'request'}),
      timelineSpan('等待','danzhengtong',1,9,{kind:'wait'}),
      timelineSpan('查询','danzhengtong',9,10,{kind:'request',call_id:3})]}],...extra});

test('parallel spans share their actual clock axis while total duration does not add overlapping work', () => {
  const data = response(undefined,{processing_seconds:99,timeline:timeline()});
  const html = renderRunHistory(data);
  assert.match(html,/全流程历时<\/span><strong>10 秒/);
  assert.match(html,/并行时间轴/);
  assert.match(html,/data-kind="recognition"[^>]*style="left:30.00000%;width:40.00000%"/);
  assert.match(html,/data-kind="recognition"[^>]*style="left:30.00000%;width:60.00000%"/);
  assert.match(html,/data-kind="lifecycle"/);
  assert.match(html,/data-kind="wait"/);
  assert.match(html,/data-kind="request"/);
  assert.doesNotMatch(html,/data-timeline-lane="[^"]*" open|data-run-details="all" open/);
  assert.ok(html.indexOf('class="run-timeline"') < html.indexOf('data-run-details="all"'));
});

test('retained provider runs stay separate and never contribute to the current elapsed time', () => {
  const current = timeline().runs[0];
  const retained = {id:'old',kind:'retained',label:'旧本地读取',started_at:'2026-10-25T10:00:00Z',
    finished_at:'2026-10-25T10:00:20Z',elapsed_seconds:20,spans:[
      {id:'old-call',label:'以前的本地识别',lane:'seal',kind:'recognition',
        started_at:'2026-10-25T10:00:00Z',finished_at:'2026-10-25T10:00:20Z',seconds:20},
    ]};
  const html = renderRunHistory(response(undefined,{incremental:true,timeline:timeline({runs:[retained,current]})}));
  assert.match(html,/此前保留的识别/);
  assert.match(html,/单独计时，不参与本次全流程历时/);
  assert.match(html,/全流程历时<\/span><strong>10 秒/);
  assert.ok(html.indexOf('data-run-kind="current"') < html.indexOf('data-run-kind="retained"'));
  assert.equal((html.match(/class="run-timeline-axis"/g)||[]).length,2);
});

test('legacy async sums cannot fabricate a process total or individual remote request positions', () => {
  const data = response([call(1),call(2,{method:'danzhengtong',seconds:100})],{
    processing_seconds:999,model_initialization_seconds:1});
  assert.equal(timelineSummary(data).seconds,null);
  const html = renderRunHistory(data);
  assert.match(html,/全流程历时<\/span><strong>未记录/);
  assert.doesNotMatch(html,/data-kind="request"|data-kind="wait"|999 秒/);
  const known = {...data,calls:[call(1,{method:'danzhengtong',seconds:100,
    transport:{started_at:time(5),finished_at:time(10),elapsed_seconds:5}})]};
  const fallback = resolveRunTimeline(known);
  assert.equal(fallback.runs.length,1);
  assert.equal(fallback.runs[0].spans.length,1);
  assert.equal(fallback.runs[0].spans[0].kind,'lifecycle');
  assert.match(renderRunTimeline(known),/不推算上传、提交、查询及等待区间/);
});

test('unknown and ongoing timelines show recorded intervals without inventing a completed total', () => {
  const running = timeline({elapsed_seconds:null,runs:[{id:'working',kind:'current',complete:false,
    elapsed_seconds:null,spans:[timelineSpan('仍在处理','danzhengtong',0,4,{kind:'lifecycle',status:'running',ongoing:true})]}]});
  const html = renderRunHistory(response([],{timeline:running}),{selectedSpan:JSON.stringify(['working','仍在处理'])});
  assert.match(html,/全流程历时<\/span><strong>未记录/);
  assert.match(html,/data-ongoing="true"/);
  assert.match(html,/记录截至/);
  assert.match(html,/记录仍在进行/);
  assert.doesNotMatch(html,/data-kind="request"|data-kind="wait"/);
  const unknown = renderRunHistory(response([],{timeline:{version:1,runs:[],elapsed_seconds:null}}));
  assert.match(unknown,/尚未记录可绘制的流程区间/);
  assert.match(unknown,/已有调用明细仍可在下方查看/);
});

test('known legacy receipt retrieval elapsed remains approximate and does not claim unrecorded finalization', () => {
  const html = renderRunHistory(response(undefined,{timeline:timeline({elapsed_seconds:13.4262,
    elapsed_label:'截至结果取回',approximate:true})}));
  assert.match(html,/截至结果取回<\/span><strong>约 13.43 秒/);
  assert.doesNotMatch(html,/全流程历时<\/span><strong>13.43/);
});

test('timeline labels escape markup and only finite recorded boundaries become bars', () => {
  const html = renderRunTimeline({timeline:timeline({runs:[{id:'<script>run</script>',kind:'current',
    label:'<img src=x onerror=x>',spans:[timelineSpan('<script>span</script>','date',0,1),
      {id:'bad',label:'missing endpoint',lane:'date',started_at:time(0),seconds:999}]}]})},
    {selectedSpan:JSON.stringify(['<script>run</script>','<script>span</script>'])});
  assert.match(html,/&lt;img src=x onerror=x&gt;/);
  assert.match(html,/&lt;script&gt;span&lt;\/script&gt;/);
  assert.doesNotMatch(html,/<script>|<img src=x|NaN|Infinity|missing endpoint/);
  assert.match(html,/<button type="button"[^>]*aria-label=/);
  assert.match(html,/role="status" aria-live="polite"/);
});

test('UTC start times display in the browser local timezone and invalid times stay unknown', () => {
  const moduleUrl = pathToFileURL(require.resolve('../static/modules/review/run_history_view.mjs')).href;
  const result = execFileSync(process.execPath, ['--input-type=module', '-e',
    `import {runTime} from ${JSON.stringify(moduleUrl)}; process.stdout.write(JSON.stringify([runTime('2026-10-26T01:20:30Z'),runTime('2026-10-26T01:20:30+00:00'),runTime('invalid'),runTime('')]));`],
    {env:{...process.env,TZ:'Asia/Shanghai'},encoding:'utf8'});
  assert.deepEqual(JSON.parse(result),['2026-10-26 09:20:30','2026-10-26 09:20:30','未记录','未记录']);
  assert.match(renderRunHistory(response([],{status:'completed'})),/已完成/);
});

test('latest input calls stay distinct, including repeated inputs, and parallel sums are not wall time', () => {
  const html = renderRunHistory(response([call(1,{input_name:'seal_crop.png'}),call(2,{input_name:'seal_crop.png'}),call(3,{seconds:3})]));
  assert.equal((html.match(/class="review-call-input">seal_crop.png/g)||[]).length,2);
  assert.match(html,/实际调用<\/span><strong>3<\/strong>/);
  assert.match(html,/复用已有识别结果 4 次，未计入实际调用/);
  assert.match(html,/处理用时 <strong>5 秒/);
  assert.match(html,/title="调用耗时合计"><small>识别累计<\/small> 7 秒/);
  assert.match(html,/并行识别时，各次耗时相加可能超过全流程历时/);
  assert.doesNotMatch(html,/data-runs-result|data-runs-more|第 \d+ 次运行|历史测试/);
  assert.match(html,/data-call-explanation="count">/);
  assert.doesNotMatch(html,/data-call-explanation="count" open|receipt.jpg/);
});

test('supplementary calls explain retained methods without claiming they ran again', () => {
  const html = renderRunHistory(response([call(1),call(2,{method:'danzhengtong',method_label:'单证通',seconds:null})], {
    incremental:true,note:'保留此前其他方法的调用明细，并补充本次单证通调用。'}));
  assert.match(html,/当前结果采用的识别调用/);
  assert.doesNotMatch(html,/仅最近一次处理/);
  assert.match(html,/保留此前其他方法/);
  assert.match(html,/单证通/);
  assert.match(html,/title="调用耗时合计"><small>识别累计<\/small> 未记录/);
});

test('remote service timings are visible above the unchanged recognition groups', () => {
  const html = renderRunHistory(response([call(1,{stage:'routing'}),call(2,{stage:'date'}),call(3)], {
    image_processing:imageProcessing(),remote_timings:[
      {method:'danzhengtong',label:'单证通',status:'applied',calls:1,request_scope:'full',
        request_seconds:2.5,elapsed_seconds:61.5,elapsed_scope:'upload_to_result',
        upload_seconds:.4,submit_seconds:.6,query_seconds:1.5,query_count:3},
      {method:'qingtong',label:'清瞳',status:'succeeded',calls:1,request_scope:'full',
        request_seconds:3.4,elapsed_seconds:99,elapsed_scope:'request',server_seconds:.42},
    ]}));
  assert.match(html,/aria-label="远程接口耗时"><h4>远程接口耗时/);
  assert.match(html,/data-remote-timing="danzhengtong"/);
  assert.match(html,/<span>请求累计<\/span><strong>2.5 秒/);
  assert.match(html,/<span>上传至结果<\/span><strong>61.5 秒/);
  assert.match(html,/<dt>上传<\/dt><dd>0.4 秒/);
  assert.match(html,/<dt>提交<\/dt><dd>0.6 秒/);
  assert.match(html,/<dt>查询 3 次<\/dt><dd>1.5 秒/);
  assert.match(html,/到结果历时含等待；请求累计不含等待间隔/);
  const qingtong = html.match(/<article[^>]*data-remote-timing="qingtong">[\s\S]*?<\/article>/)[0];
  assert.match(qingtong,/<span>请求耗时<\/span><strong>3.4 秒/);
  assert.match(qingtong,/<span>服务端处理<\/span><strong>0.42 秒/);
  assert.match(qingtong,/本机请求端到端耗时分别记录/);
  assert.doesNotMatch(qingtong,/99 秒|到结果|上传|查询/);
  assert.ok(html.indexOf('class="review-remote-timings"') < html.indexOf('data-call-group="routing"'));
  for (const stage of ['routing','date','seal']) assert.match(html,new RegExp(`data-call-group="${stage}">`));
  assert.match(html,/data-image-processing="steps">/);
  assert.match(html,/实际调用<\/span><strong>3<\/strong>/);
  assert.match(html,/处理用时 <strong>未记录/);
});

test('historic remote scopes stay explicit and missing values never become zero', () => {
  const html = renderRunHistory(response([], {remote_timings:[
    {method:'danzhengtong',label:'单证通',status:'received',calls:1,request_scope:'submit_query',
      request_seconds:2,elapsed_seconds:120,elapsed_scope:'submission_to_result',elapsed_approximate:true,
      upload_seconds:null,submit_seconds:0,query_seconds:null,query_count:null},
    {method:'qingtong',label:'清瞳',status:'unknown',calls:null,request_scope:'legacy_call',
      request_seconds:null,server_seconds:null,note:'历史记录未保存请求耗时。'},
  ]}));
  assert.match(html,/<span>提交与查询<\/span><strong>2 秒/);
  assert.match(html,/<span>受理至结果<\/span><strong>约 120 秒/);
  assert.match(html,/<dt>上传<\/dt><dd>未记录/);
  assert.match(html,/<dt>提交<\/dt><dd>0 秒/);
  assert.match(html,/<dt>查询<\/dt><dd>未记录/);
  assert.match(html,/<span>历史调用耗时<\/span><strong>未记录/);
  assert.match(html,/<span>服务端处理<\/span><strong>未记录/);
  assert.match(html,/历史记录未保存请求耗时/);
  assert.doesNotMatch(html,/查询 0 次|约 未记录/);
  const invalid = renderRunHistory(response([], {remote_timings:[
    {method:'danzhengtong',request_seconds:false,elapsed_seconds:-1,upload_seconds:NaN,
      submit_seconds:Infinity,query_seconds:'',elapsed_approximate:true},
  ]}));
  const card = invalid.match(/<article[^>]*data-remote-timing="danzhengtong">[\s\S]*?<\/article>/)[0];
  assert.equal((card.match(/<strong>未记录<\/strong>/g)||[]).length,2);
  assert.doesNotMatch(invalid,/约 未记录|<dd>0 秒/);
});

test('remote timing evidence remains visible when physical call details are unavailable and escapes all text', () => {
  const html = renderRunHistory(response([], {available:false,remote_timings:[
    {method:'qingtong',label:'<img src=x onerror=alert(1)>',status:'<script>x</script>',
      request_scope:'legacy_call',request_seconds:null,server_seconds:.42,note:'<b>服务端返回</b>'},
  ]}));
  assert.match(html,/远程接口耗时/);
  assert.match(html,/<span>服务端处理<\/span><strong>0.42 秒/);
  assert.match(html,/本次识别调用明细未记录/);
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html,/&lt;script&gt;x&lt;\/script&gt;/);
  assert.match(html,/&lt;b&gt;服务端返回&lt;\/b&gt;/);
  assert.doesNotMatch(html,/<script>|<img src=x|<b>服务端/);
  for (const remote_timings of [undefined,null,[],{}]) {
    assert.doesNotMatch(renderRunHistory(response([], {remote_timings})),/review-remote-timings|远程接口耗时/);
  }
});

test('partially measured requests and failed uploads do not claim a complete total or an invented recognition call', () => {
  const partial = renderRunHistory(response([], {remote_timings:[
    {method:'danzhengtong',label:'单证通',status:'failed',calls:0,request_scope:'partial',
      request_seconds:1.2,upload_seconds:1.2,submit_seconds:null,query_seconds:null,query_count:0,
      elapsed_seconds:null,note:'上传失败，提交与查询未执行。'},
  ]}));
  assert.match(partial,/<span>已记录请求耗时<\/span><strong>1.2 秒/);
  assert.match(partial,/data-state="failed">处理失败/);
  assert.match(partial,/<dt>上传<\/dt><dd>1.2 秒/);
  assert.match(partial,/<dt>提交<\/dt><dd>未记录/);
  assert.match(partial,/<small>0 次识别<\/small>/);
  assert.match(partial,/上传失败，提交与查询未执行/);
  assert.match(partial,/已记录耗时仅包含已采集请求/);
  const unknown = renderRunHistory(response([], {remote_timings:[
    {method:'qingtong',label:'清瞳',status:'unknown',calls:null,request_scope:'unknown'},
  ]}));
  assert.doesNotMatch(unknown,/\d+ 次识别/);
  assert.match(unknown,/<span>请求耗时<\/span><strong>未记录/);
});

test('non-request elapsed is labelled as waiting and scheduling only when both scopes agree', () => {
  const timing = {method:'danzhengtong',request_scope:'full',request_seconds:7.5962,
    elapsed_scope:'upload_to_result',elapsed_seconds:13.4237};
  const html = renderRunHistory(response([], {remote_timings:[timing]}));
  assert.match(html,/<dt>等待\/调度间隔<\/dt><dd>5.83 秒/);
  assert.doesNotMatch(html,/<span>服务端处理/);
  for (const invalid of [{request_scope:'submit_query'},{request_scope:'partial'},
    {elapsed_scope:'submission_to_result'},{elapsed_seconds:3},{request_seconds:null}]) {
    assert.doesNotMatch(renderRunHistory(response([], {remote_timings:[{...timing,...invalid}]})),/<dt>等待\/调度间隔/);
  }
});

test('initialization is displayed separately and included in full elapsed without changing call counts', () => {
  const html = renderRunHistory(response(undefined,{processing_seconds:11.6288,model_initialization_seconds:5.9647}));
  assert.match(html,/处理用时 <strong>5.66 秒/);
  assert.match(html,/模型初始化<\/span><strong>5.96 秒/);
  assert.match(html,/已计入全流程历时/);
  assert.match(html,/实际调用<\/span><strong>3<\/strong>/);
  assert.match(html,/全流程历时<\/span><strong>11.63 秒/);
  const unknown = renderRunHistory(response(undefined,{model_initialization_seconds:null}));
  assert.match(unknown,/处理用时 <strong>未记录/);
  assert.match(unknown,/此记录未单独采集/);
});

test('image operations display separately without adding their count or cumulative time to OCR totals', () => {
  const html = renderRunHistory(response(undefined,{processing_seconds:7,model_initialization_seconds:2,
    image_processing:imageProcessing()}));
  assert.match(html,/data-image-processing="steps">/);
  assert.doesNotMatch(html,/data-image-processing="steps" open/);
  assert.match(html,/图像处理累计耗时"><small>累计<\/small> 8 秒/);
  assert.match(html,/<td>旋转校正<\/td><td>客户印章<\/td><td>20<\/td><td>3 秒/);
  assert.match(html,/<td>裁剪<\/td><td>签收日期<\/td><td>4<\/td><td>0.5 秒/);
  assert.match(html,/文字展开<small class="review-image-processing-failed">失败 1 次/);
  assert.match(html,/实际调用<\/span><strong>3<\/strong>/);
  assert.match(html,/处理用时 <strong>5 秒/);
  assert.match(html,/处理用时已包含图像处理/);
  assert.doesNotMatch(html,/处理用时 <strong>13 秒/);
});

test('unmeasured image processing stays unknown while measured zero and escaped labels remain truthful', () => {
  for (const image_processing of [undefined,null,{seconds:null,steps:[]},{seconds:2,steps:null}]) {
    const html = renderRunHistory(response(undefined,{image_processing}));
    assert.match(html,/图像处理耗时未单独采集，重新测试后可查看/);
    assert.doesNotMatch(html,/data-image-processing=/);
  }
  const zero = renderRunHistory(response(undefined,{image_processing:{seconds:0,steps:[]}}));
  assert.match(zero,/图像处理累计耗时"><small>累计<\/small> 0 秒/);
  assert.doesNotMatch(zero,/图像处理耗时未单独采集/);
  const html = renderRunHistory(response(undefined,{image_processing:{seconds:.5,steps:[
    {stage:'custom',stage_label:'<script>x</script>',label:'<img src=x onerror=x>',calls:1,seconds:.5,failed:0},
  ]}}),{processingOpen:true});
  assert.match(html,/data-image-processing="steps" open/);
  assert.match(html,/&lt;img src=x onerror=x&gt;/);
  assert.match(html,/&lt;script&gt;x&lt;\/script&gt;/);
  assert.doesNotMatch(html,/<script>|<img src=x/);
});

test('historic timing can use the matching open receipt but never a different run or failed retry', () => {
  const data = response(undefined,{model_initialization_seconds:null});
  const item = {id:9,processing_seconds:5,queue:{started_at:time(0)},
    processing_timings:{steps:{paddle_model_setup:{seconds:2}}}};
  const html = renderRunHistory(data,{timingContext:item});
  assert.match(html,/处理用时 <strong>3 秒/);
  assert.match(html,/模型初始化<\/span><strong>2 秒/);
  for (const context of [{...item,id:10},{...item,queue:{started_at:time(50)}},{...item,processing_seconds:6}]) {
    assert.match(renderRunHistory(data,{timingContext:context}),/处理用时 <strong>未记录/);
  }
  assert.match(renderRunHistory({...data,status:'failed'},{timingContext:item}),/处理用时 <strong>未记录/);
});

test('all groups default to collapsed, errors and names escape markup, and images remain in the panel', () => {
  const html = renderRunHistory(response([
    call(1,{stage:'routing',input_name:'whole.jpg'}),
    call(2,{input_name:'folder/<script>x</script>.png',method_label:'<b>OCR</b>',status:'failed',error:'<img src=x onerror=alert(1)>',image_url:'/files/samples/artifacts/a/seal.png'}),
  ]));
  assert.match(html,/data-call-group="seal">/);
  assert.doesNotMatch(html,/<details\b[^>]*\sopen(?:\s|>)/);
  assert.doesNotMatch(renderRunHistory(response([call(1,{stage:'routing'})])),/<details\b[^>]*\sopen(?:\s|>)/);
  assert.match(html,/data-call-image="2"/);
  assert.match(html,/src="\/files\/samples\/artifacts\/a\/seal.png"/);
  assert.match(html,/&lt;b&gt;OCR&lt;\/b&gt;/);
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html,/<script>|<img src=x|target="_blank"/);
  assert.match(html,/1 次失败/);
  assert.match(html,/640 × 180/);
});

test('preview URLs permit only existing local artifacts and reject traversal or executable URLs', () => {
  for (const url of ['/files/artifacts/abc/seal.png','/files/samples/artifacts/a/seal.png','/files/previews/a/page.jpg','/files/artifacts/run/印章%201.png']) {
    assert.equal(callImageUrl(url),url);
  }
  for (const url of ['javascript:alert(1)','https://example.com/a.png','//example.com/a.png','data:image/png;base64,AAAA',
    '/files/artifacts/../secret.png','/files/artifacts/%2e%2e/secret.png','/files/artifacts/a%5cb.png','/files/artifacts/%00x.png',
    '/files/artifacts/%bad.png','/files/artifacts/raw space.png','/files/artifacts/%09x.png','/files/artifacts/%7fx.png','/files/customer/file.jpg']) assert.equal(callImageUrl(url),'');
  assert.doesNotMatch(renderRunHistory(response([call(1,{image_url:'https://example.com/pixel'})])),/data-call-image/);
});

test('missing historical measurements show dashes, while explicitly recorded zero stays zero', () => {
  const old = renderRunHistory(response([],{available:false,summary:{total:12,succeeded:12},note:'旧记录未保存调用明细，请重新测试。'}));
  assert.equal((old.match(/<strong>—<\/strong>/g)||[]).length,3);
  assert.match(old,/旧记录未保存调用明细，请重新测试/);
  assert.doesNotMatch(old,/<strong>12<\/strong>|处理用时/);
  const interrupted = renderRunHistory(response([],{available:false,status:'interrupted',summary:null,
    note:'最近一次识别已中断，未保存完整调用明细。'}));
  assert.match(interrupted,/data-state="interrupted">处理中断/);
  assert.equal((interrupted.match(/<strong>—<\/strong>/g)||[]).length,3);
  assert.match(interrupted,/最近一次识别已中断/);
  const empty = renderRunHistory(response([],{summary:{total:0,succeeded:0,failed:0,cache_hits:1},processing_seconds:0}));
  assert.match(empty,/实际调用<\/span><strong>0<\/strong>/);
  assert.match(empty,/没有发生实际识别调用/);
  assert.equal(runDuration(null),'未记录'); assert.equal(runDuration(undefined),'未记录');
  assert.equal(runDuration(''),'未记录'); assert.equal(runDuration(false),'未记录');
  assert.equal(runDuration(0),'0 秒'); assert.equal(runDuration(-1),'未记录');
  assert.equal(runDuration(.001),'< 0.01 秒');
  assert.match(renderRunHistory(response([call(1,{seconds:null}),call(2)])),/title="调用耗时合计"><small>识别累计<\/small> 未记录/);
});

function harness() {
  const nodes = new Map(), calls = [], environment = {}, document = makeNode();
  environment.document = document;
  function makeNode() {
    const handlers = {}, attributes = {}, classes = new Set();
    let html = '', details = [], spans = [];
    const decode = value => value.replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
    return {hidden:false,inert:false,disabled:false,isConnected:true,textContent:'',dataset:{},scrollTop:0,
      children:[],parentNode:null,nextSibling:null,open:false,
      handlers,attributes,classes,
      classList:{add:key=>classes.add(key),remove:key=>classes.delete(key)},
      addEventListener(type,fn) { (handlers[type] ||= []).push(fn); },
      async emit(type,event = {}) { for (const fn of handlers[type] || []) await fn(event); },
      setAttribute(key,value) { attributes[key] = value; }, getAttribute:key=>attributes[key],
      focus() { document.activeElement = this; },
      appendChild(node) { if (node.parentNode) node.parentNode.children = node.parentNode.children.filter(child=>child!==node); this.children.push(node); node.parentNode=this; return node; },
      insertBefore(node) { this.appendChild(node); },
      showModal() { this.open=true; }, close() { this.open=false; },
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = value;
        details = [...html.matchAll(/<details[^>]*(data-call-group|data-call-image|data-call-explanation|data-image-processing|data-run-details|data-timeline-lane)="([^"]*)"([^>]*)>/g)]
          .map(match=>({open:match[3].includes(' open'),attr:match[1],key:decode(match[2]),getAttribute(){return this.key;}}));
        spans = [...html.matchAll(/<button[^>]*data-timeline-span="([^"]*)"[^>]*data-timeline-occurrence="([^"]*)"[^>]*>/g)]
          .map(match=>({key:decode(match[1]),occurrence:match[2],getAttribute(name){return name==='data-timeline-span'?this.key:this.occurrence;},focus(){document.activeElement=this;},closest(selector){return selector==='[data-timeline-span]'?this:null;}}));
      },
      querySelectorAll(selector) { return selector==='[data-timeline-span]' ? spans : details.filter(node=>`[${node.attr}]` === selector); },
    };
  }
  document.body = makeNode(); document.createElement = () => makeNode();
  const $ = selector => nodes.get(selector) || document.body.children.find(node=>`#${node.id}`===selector) || null;
  for (const selector of ['#review-runs-open','[data-run-count]','#review-dialog']) nodes.set(selector,makeNode());
  const services = Object.fromEntries(['samples','records'].map(source=>[source,{recognitionCalls:id=>
    new Promise((resolve,reject)=>calls.push({source,id,resolve,reject}))}]));
  const history = createReviewRunHistory({environment,ui:{$},services});
  history.initialize();
  function mount(id = 9,sample = true) {
    for (const selector of ['#review-runs-panel','#review-runs-body','#review-runs-title','.review-layout','#review-form','[data-runs-close]']) nodes.set(selector,makeNode());
    $('#review-dialog').open = true;
    $('#review-runs-panel').hidden = true;
    $('.review-layout').appendChild($('#review-runs-panel'));
    $('#review-form').editedValue = '尚未保存的标注';
    history.setContext({id,filename:`${id}.jpg`},{sample});
  }
  function target(selector) { const node = makeNode(); node.closest = key=>key === selector ? node : null; return node; }
  const click = selector => $('#review-runs-panel').emit('click',{target:target(selector)});
  mount();
  return {$,history,calls,document,mount,click};
}

test('wide dialog loads lazily without hiding or mutating the review form and returns focus', async () => {
  const h = harness(), form = h.$('#review-form');
  form.scrollTop = 735; form.scrollLeft = 28;
  assert.equal(h.calls.length,0);
  const opening = h.history.open();
  assert.equal(form.hidden,false); assert.equal(form.inert,false);
  assert.equal(form.scrollTop,735); assert.equal(form.scrollLeft,28);
  assert.equal(h.$('#review-runs-dialog').open,true);
  assert.equal(h.$('#review-runs-dialog').children[0],h.$('#review-runs-panel'));
  assert.equal(h.$('.review-layout').classes.has('run-history-open'),false);
  assert.equal(h.history.isOpen(),true);
  h.calls[0].resolve(response([call(1,{image_url:'/files/artifacts/x/seal.png'})],{image_processing:imageProcessing()})); await opening;
  const body = h.$('#review-runs-body');
  assert.doesNotMatch(body.innerHTML,/<details\b[^>]*\sopen(?:\s|>)/);
  for (const selector of ['[data-call-group]','[data-call-image]','[data-call-explanation]','[data-image-processing]']) {
    body.querySelectorAll(selector)[0].open = true;
  }
  assert.equal(h.$('[data-run-count]').textContent,'3 次');
  assert.equal(h.$('#review-runs-open').attributes['aria-expanded'],'true');
  await h.click('[data-runs-close]');
  assert.equal(h.history.isOpen(),false);
  assert.equal(h.$('#review-runs-dialog').open,false);
  assert.equal(form.hidden,false); assert.equal(form.inert,false);
  assert.equal(form.scrollTop,735); assert.equal(form.scrollLeft,28);
  assert.equal(form.editedValue,'尚未保存的标注');
  assert.equal(h.document.activeElement,h.$('#review-runs-open'));
  await h.history.open();
  assert.equal(h.calls.length,1);
  assert.doesNotMatch(body.innerHTML,/<details\b[^>]*\sopen(?:\s|>)/);
});

test('late results cannot overwrite a closed panel or a different receipt and sources stay isolated', async () => {
  const h = harness();
  const old = h.history.open(); h.history.close();
  h.calls[0].resolve(response()); await old;
  assert.doesNotMatch(h.$('#review-runs-body').innerHTML,/Paddle/);
  const next = h.history.open(); h.history.clear(); h.mount(10,false);
  const newest = h.history.open();
  assert.deepEqual(h.calls.map(call=>[call.source,call.id]),[['samples',9],['samples',9],['records',10]]);
  h.calls[2].resolve(response([call(1,{input_name:'new.jpg'})],{filename:'new.jpg'})); await newest;
  h.calls[1].resolve(response([call(1,{input_name:'old.jpg'})],{filename:'old.jpg'})); await next;
  assert.match(h.$('#review-runs-body').innerHTML,/new.jpg/);
  assert.doesNotMatch(h.$('#review-runs-body').innerHTML,/old.jpg/);
});

test('Escape and native dialog cancellation close only call details and preserve parent review edits', async () => {
  const h = harness(), opening = h.history.open(); h.calls[0].resolve(response()); await opening;
  const events = [];
  const event = {key:'Escape',preventDefault(){events.push('prevent');},stopPropagation(){events.push('stop');},stopImmediatePropagation(){events.push('immediate');}};
  await h.document.emit('keydown',event);
  assert.equal(h.history.isOpen(),false); assert.equal(h.$('#review-form').editedValue,'尚未保存的标注');
  assert.equal(h.$('#review-runs-dialog').open,false); assert.equal(h.$('#review-dialog').open,true);
  assert.deepEqual(events,['prevent','immediate','stop']);
  await h.document.emit('keydown',event); assert.equal(events.length,3);
  await h.history.open(); await h.$('#review-runs-dialog').emit('cancel',event);
  assert.equal(h.history.isOpen(),false); assert.equal(h.$('#review-dialog').open,true);
});

test('timeline selection keeps lane expansion, scroll, keyboard focus and current selection across refresh', async () => {
  const h = harness(), opening = h.history.open();
  h.calls[0].resolve(response(undefined,{timeline:timeline()})); await opening;
  const body = h.$('#review-runs-body'), lane = JSON.stringify(['current','danzhengtong']);
  body.querySelectorAll('[data-timeline-lane]').find(node=>node.key===lane).open=true;
  body.scrollTop=240;
  const selected = JSON.stringify(['current','查询']);
  const span = body.querySelectorAll('[data-timeline-span]').find(node=>node.key===selected && node.occurrence==='detail');
  let prevented=false;
  await h.$('#review-runs-panel').emit('click',{target:span,preventDefault(){prevented=true;},stopPropagation(){}});
  assert.equal(prevented,true);
  assert.match(body.innerHTML,/class="run-timeline-selection" role="status"/);
  assert.match(body.innerHTML,/<dt>识别调用<\/dt><dd>#3/);
  assert.equal(body.querySelectorAll('[data-timeline-lane]').find(node=>node.key===lane).open,true);
  assert.equal(body.scrollTop,240);
  assert.equal(h.document.activeElement.key,selected);
  assert.equal(h.document.activeElement.occurrence,'detail');
  body.querySelectorAll('[data-run-details]')[0].open=true;
  const refreshing=h.click('[data-runs-refresh]');
  h.calls[1].resolve(response(undefined,{timeline:timeline()})); await refreshing;
  assert.match(body.innerHTML,/class="run-timeline-selection" role="status"/);
  assert.equal(body.querySelectorAll('[data-timeline-lane]').find(node=>node.key===lane).open,true);
  assert.equal(body.querySelectorAll('[data-run-details]')[0].open,true);
  assert.equal(body.scrollTop,240);
  await h.history.close();
  assert.equal(h.$('#review-form').hidden,false); assert.equal(h.$('#review-form').inert,false);
});

test('closing the parent review also closes the child dialog and invalidates pending detail data', async () => {
  const h=harness(), opening=h.history.open();
  h.$('#review-dialog').open=false; await h.$('#review-dialog').emit('close');
  assert.equal(h.$('#review-runs-dialog').open,false);
  h.calls[0].resolve(response(undefined,{timeline:timeline()})); await opening;
  assert.equal(h.history.isOpen(),false);
  assert.equal(h.$('#review-runs-body').innerHTML,'');
});

test('refresh replaces the latest calls while preserving expanded groups, image previews and scroll', async () => {
  const h = harness(), opening = h.history.open();
  h.calls[0].resolve(response([call(1,{image_url:'/files/artifacts/x/seal.png'}),call(2,{stage:'date'})],{image_processing:imageProcessing()})); await opening;
  const body = h.$('#review-runs-body');
  body.scrollTop = 410;
  body.querySelectorAll('[data-call-group]').find(row=>row.key==='date').open = true;
  body.querySelectorAll('[data-call-image]')[0].open = true;
  body.querySelectorAll('[data-call-explanation]')[0].open = true;
  body.querySelectorAll('[data-image-processing]')[0].open = true;
  const refreshing = h.click('[data-runs-refresh]');
  assert.match(body.innerHTML,/data-runs-refresh disabled/);
  await h.click('[data-runs-refresh]'); assert.equal(h.calls.length,2);
  h.calls[1].resolve(response([call(1,{input_name:'latest.png',image_url:'/files/artifacts/x/seal.png'}),call(2,{stage:'date'})],{image_processing:imageProcessing()})); await refreshing;
  assert.match(body.innerHTML,/latest.png/); assert.doesNotMatch(body.innerHTML,/seal_1.png/);
  assert.match(body.innerHTML,/data-call-group="date" open/);
  assert.match(body.innerHTML,/data-call-image="1" open/);
  assert.match(body.innerHTML,/data-call-explanation="count" open/);
  assert.match(body.innerHTML,/data-image-processing="steps" open/);
  assert.equal(body.scrollTop,410);
});

test('refreshing an unrecorded in-progress snapshot leaves newly available groups collapsed', async () => {
  const h = harness(), opening = h.history.open();
  h.calls[0].resolve(response([],{available:false,status:'running',summary:null,
    note:'本次正在识别，完成后可查看实际调用次数和耗时。'})); await opening;
  assert.equal(h.$('[data-run-count]').textContent,'—');
  const refresh = h.click('[data-runs-refresh]'); h.calls[1].resolve(response()); await refresh;
  const body = h.$('#review-runs-body');
  assert.match(body.innerHTML,/data-call-group="seal">/);
  body.querySelectorAll('[data-call-group]')[0].open = false;
  const again = h.click('[data-runs-refresh]'); h.calls[2].resolve(response()); await again;
  assert.doesNotMatch(body.innerHTML,/data-call-group="seal" open/);
});

test('refresh errors retain existing data and first-load failures escape errors and allow retry', async () => {
  const h = harness(), opening = h.history.open();
  h.calls[0].reject(Error('<broken>')); await opening;
  assert.match(h.$('#review-runs-body').innerHTML,/&lt;broken&gt;/);
  assert.match(h.$('#review-runs-body').innerHTML,/data-runs-refresh/);
  const retry = h.click('[data-runs-refresh]'); h.calls[1].resolve(response()); await retry;
  assert.match(h.$('#review-runs-body').innerHTML,/seal_1.png/);
  assert.doesNotMatch(h.$('#review-runs-body').innerHTML,/broken/);
  const refresh = h.click('[data-runs-refresh]'); h.calls[2].reject(Error('暂时断开')); await refresh;
  assert.match(h.$('#review-runs-body').innerHTML,/暂时断开/);
  assert.match(h.$('#review-runs-body').innerHTML,/seal_1.png/);
});

test('a delayed old close event cannot clear a reopened dialog and dynamic panels bind once', async () => {
  const h = harness();
  h.history.setContext({id:9},{sample:true});
  assert.equal(h.$('#review-runs-panel').handlers.click.length,1);
  await h.$('#review-dialog').emit('close');
  assert.equal(h.$('#review-runs-open').hidden,false);
  const opening = h.history.open(); h.calls[0].resolve(response([],{available:false})); await opening;
  assert.equal(h.$('[data-run-count]').textContent,'—');
  h.$('#review-dialog').open = false; await h.$('#review-dialog').emit('close');
  assert.equal(h.history.isOpen(),false); assert.equal(h.$('#review-runs-open').hidden,true);
  h.mount(10); assert.equal(h.$('#review-runs-panel').handlers.click.length,1);
});
