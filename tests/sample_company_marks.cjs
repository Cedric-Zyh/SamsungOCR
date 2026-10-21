const test = require('node:test');
const assert = require('node:assert/strict');
const {companyMatchesMark, companyRowMarkup} = require('../static/modules/samples/company_marks.mjs');
const {createApiClients} = require('../static/modules/core/api.mjs');
const {sampleCompanyProgress} = require('../static/modules/samples/center.mjs');

test('manual filters are independent of automatic completion and support unmarked companies', () => {
  assert(companyMatchesMark(undefined, 'unmarked'));
  assert(companyMatchesMark({state:'issue'}, ''));
  assert(!companyMatchesMark({state:'checked'}, 'pending'));
  assert(companyMatchesMark({state:'checked',has_new_results:true}, 'checked'));
});

test('company navigation stays compact, escapes names and preserves completion color', () => {
  const html = companyRowMarkup({name:'甲<公司>',count:2}, sampleCompanyProgress([{status:'已完成'},{status:'已完成'}]),
    true, {state:'checked',note:'<img onerror=x>',has_new_results:true});
  assert.match(html, /data-progress="complete"/);
  assert(!html.includes('data-mark='));
  assert(!html.includes('有新测试结果'));
  assert(!html.includes('onerror'));
  assert.match(html, /甲&lt;公司&gt;/);
  assert.equal((html.match(/<button /g) || []).length, 1);
  const all = companyRowMarkup({name:'',count:0},sampleCompanyProgress([]),false);
  assert(!all.includes('data-mark-company'));
});

test('company marker writes stay in the sample namespace and carry the observed result version', async () => {
  const calls = [];
  const payload = {company:'甲公司',state:'checked',note:'已核对',revision:2,reviewed:true,result_version:'snapshot'};
  await createApiClients(async (...args) => calls.push(args)).samples.saveCompanyMark(payload);
  assert.deepEqual(calls, [['/api/samples/company-mark',{method:'POST',json:payload}]]);
});

test('seal check is independent of completion and describes partial coverage', () => {
  const row = {name:'甲公司',count:3};
  const completed = companyRowMarkup(row, sampleCompanyProgress([{status:'已完成'}]), false);
  assert(!completed.includes('✓'));
  const registered = companyRowMarkup(row, sampleCompanyProgress([]), false,
    {has_standard:true,matched:1,total:2,unlabeled:1});
  assert.match(registered, /aria-label="印章中心已有标准章"/);
  assert.match(registered, /已收录 1\/2 种签章要求；1 张样单未标注签章要求/);
  const all = companyRowMarkup({name:'',count:3},sampleCompanyProgress([]),false,{has_standard:true});
  assert(!all.includes('✓'));
});
