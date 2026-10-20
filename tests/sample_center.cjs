const test = require('node:test');
const assert = require('node:assert/strict');
const {sampleScope, sampleCard, sampleImageUrl} = require('../static/modules/samples/center.mjs');
const {createApiClients} = require('../static/modules/core/api.mjs');
const {ReceiptWorkbench} = require('../static/modules/core/workbench.mjs');

test('company, filename and status filters compose without broadening a company', () => {
  const items = [
    {company:'甲公司', filename:'123.jpg', status:'未测试'},
    {company:'甲公司', filename:'456.jpg', status:'已完成'},
    {company:'乙公司', filename:'123.jpg', status:'未测试'},
  ];
  assert.deepEqual(sampleScope(items, {company:'甲公司', search:' 123 ', status:'未测试'}), [items[0]]);
  assert.deepEqual(sampleScope(items, {company:'不存在'}), []);
  assert.equal(sampleScope(items, {company:'甲公司'}).length, 2);
});

test('cards escape customer-controlled text and prevent running active tests', () => {
  const html = sampleCard({filename:'<img>.jpg', status:'测试中', job_status:'queued', error:'<script>x</script>'}, true);
  assert.match(html, /data-sample-run disabled/);
  assert.match(html, /data-sample-result disabled/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(sampleImageUrl('a #.jpg'), /a%20%23.jpg$/);
});

test('sample clients always use independent endpoints even for colliding result ids', async () => {
  const calls = [];
  const clients = createApiClients(async (...args) => calls.push(args));
  await clients.samples.get(1);
  await clients.samples.reviewHistory(1);
  await clients.samples.groundTruth(1);
  await clients.samples.run(['123.jpg']);
  await clients.samples.saveStandard(1, {candidate_id:'sample-seal',standard_revision:0});
  assert(calls.every(([url]) => url.startsWith('/api/samples/')));
  assert.deepEqual(calls[3][1].json, {filenames:['123.jpg']});
  assert.equal(calls[4][0], '/api/samples/results/1/seal-standard');
});

test('sample selected-seal previews never reference the production result id route', () => {
  const sources = ReceiptWorkbench.imageSources({id:1, sample_center:true,
    preview_url:'/files/samples/previews/test.jpg',
    seal_check:{api:{ok:true}, dual_check:{policy:'qingtong_any_channel', selected:{xyxy:[1,1,20,20]}}}});
  assert(sources.some(source => source.url === '/files/sample-selected-seal/1.png'));
  assert(!sources.some(source => source.url.startsWith('/files/selected-seal/')));
});
