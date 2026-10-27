const assert = require('node:assert/strict');
const test = require('node:test');
const {describeStandard, groupStandards} = require('../static/modules/seals/standard_groups.mjs');

test('server terms control name directories and preserve independent identity codes',()=>{
  const make=(requirement,terms)=>({requirement,name_terms:{original:requirement,...terms}});
  const company='深圳欧瑞特供应链管理有限公司';
  const items=[make(company+'福州仓库收货专用章(1)',{company,branch:'福州仓库',purpose:'收货专用章',number:'(1)',groupable:true}),
    make(company+'南京分公司收货章',{company,branch:'南京分公司',purpose:'收货章',groupable:true})];
  assert.equal(groupStandards(items).length,1);
  assert.equal(describeStandard(items[0]).label,'福州仓库 · 收货专用章(1)');
  const codes=['6055487','4910964'].map(code=>make('三星电子服务中心授权代码:'+code,
    {company:'三星电子服务中心',parse_status:'parsed',groupable:false,
      identity_fields:[{kind:'authorization_code',value:code}]}));
  const [directory] = groupStandards(codes);
  assert.equal(groupStandards(codes).length,1);
  assert.equal(directory.sameName,true);
  assert.deepEqual(directory.items.map(item=>item.description.identities),['授权代码 6055487','授权代码 4910964']);
  assert.deepEqual(directory.items.map(item=>item.standard),codes);
  assert(codes.every(item=>item.name_terms.groupable === false));
  const store=make('华润万象汇三星授权体验店',{company:'华润万象汇三星授权体验店',groupable:true});
  assert.equal(describeStandard(store).company,'华润万象汇三星授权体验店');
  // Stale metadata cannot override a changed requirement.
  assert.notEqual(describeStandard({...store,requirement:'其他有限公司公章'}).company,store.requirement);
});

test('complete generic-center metadata groups stations and keeps branch and phone distinctions visible',()=>{
  const make=(id,company,branch,field)=>({id,requirement:`${company}${branch || ''}${field.value}`,
    name_terms:{original:`${company}${branch || ''}${field.value}`,company,branch,
      parse_status:'parsed',groupable:false,identity_fields:[field]}});
  const standards=[
    make(1,'三星电子维修中心','',{kind:'station_code',value:'5972036'}),
    make(2,'三星电子维修中心','',{kind:'station_code',value:'2310637'}),
    make(3,'三星电子服务中心','上海信',{kind:'station_code',label:'站代码',value:'6237106'}),
    make(4,'三星电子服务中心','',{kind:'authorization_code',value:'6055487'}),
    make(5,'三星电子授权服务中心','',{kind:'phone',value:'0431-81813021'}),
    make(6,'三星电子授权服务中心','',{kind:'phone',value:'0431-88693789'}),
  ];
  const groups=groupStandards(standards);
  assert.deepEqual(groups.map(group=>group.items.map(item=>item.standard.id)),[[1,2],[3,4],[5,6]]);
  assert(groups.every(group=>group.sameName));
  assert.equal(groups[1].items[0].description.label,'上海信 · 站代码 6237106');
  assert.equal(groups[2].items[1].description.label,'电话 0431-88693789');
});

test('same-name warehouse directories retain literal codes and reversed stamp-number choices',()=>{
  const requirements=[['020DCH','①/②'],['020DCH','②/①'],['O20DCH','①/②'],['020DCM','①/②']];
  const standards=requirements.map(([code,number],index)=>{
    const requirement=`广州花东仓${code}收货专用章${number}`;
    return {id:index+1,requirement,name_terms:{original:requirement,company:'广州花东仓',
      parse_status:'parsed',groupable:false,purpose:'收货专用章',number,
      identity_fields:[{kind:'warehouse_code',label:'仓库代码',value:code}]}};
  });
  const groups=groupStandards(standards);
  assert.equal(groups.length,1);
  assert.equal(groups[0].title,'广州花东仓');
  assert.equal(groups[0].sameName,false);
  assert.deepEqual(groups[0].items.map(item=>item.standard),standards);
  assert.deepEqual(groups[0].items.map(item=>item.description.label),[
    '仓库代码 020DCH · 收货专用章①/②','仓库代码 020DCH · 收货专用章②/①',
    '仓库代码 O20DCH · 收货专用章①/②','仓库代码 020DCM · 收货专用章①/②',
  ]);
});

test('partial metadata preserves original entries even if an old grouping flag was true',()=>{
  const requirements=['甲有限公司北售后服务专用章','甲有限公司南售后服务专用章'];
  const standards=requirements.map(requirement=>({requirement,recognized:'甲有限公司收货章',
    name_terms:{original:requirement,company:'甲有限公司',parse_status:'partial',
      groupable:true,unclassified:requirement.includes('北')?'北':'南'}}));
  assert.deepEqual(groupStandards(standards).map(group=>group.title),requirements);
  assert(standards.every(standard=>describeStandard(standard).company === ''));
});

test('alternative descriptions keep both purposes and their own stamp numbers',()=>{
  const company='青岛和沃电子技术有限公司';
  const requirement=company+'业务专用章/'+company+'维修业务专用章（1）';
  const standard={requirement,name_terms:{original:requirement,company,groupable:true,
    alternatives:[{purpose:'业务专用章',number:''},{purpose:'维修业务专用章',number:'(1)'}]}};
  const description=describeStandard(standard);
  assert.equal(description.company,company);
  assert.equal(description.seal,'业务专用章 / 维修业务专用章(1)');
});

test('alternative-only identity fields remain attached to their own wording',()=>{
  const company='青岛和沃电子技术有限公司';
  const requirement=company+'业务专用章授权代码:11/'+company+'维修业务专用章（1）授权代码:22';
  const standard={requirement,name_terms:{original:requirement,company,parse_status:'parsed',groupable:false,
    identity_fields:[],alternatives:[
      {purpose:'业务专用章',identity_fields:[{kind:'authorization_code',value:'11'}]},
      {purpose:'维修业务专用章',number:'(1)',identity_fields:[{kind:'authorization_code',value:'22'}]},
    ]}};
  const description=describeStandard(standard);
  assert.equal(description.seal,'业务专用章 · 授权代码 11 / 维修业务专用章(1) · 授权代码 22');
  assert.equal(description.identities,'');
});

test('all branches of the exact legal company group together with distinct purposes and seal numbers', () => {
  const company = '深圳欧瑞特供应链管理有限公司';
  const standards = [
    {id: 7, requirement: `${company}兰州分公司收货专用章（1）`},
    {id: 8, requirement: `${company}武汉分公司收货专用章（2）`},
    {id: 9, requirement: `${company}兰州分公司业务章（3）`},
    {id: 10, requirement: `${company}公章`},
  ];
  const [group] = groupStandards(standards);
  assert.equal(group.title, company);
  assert.equal(group.items.length, 4);
  assert.deepEqual(group.items.map(item => item.description.label), [
    '兰州分公司 · 收货专用章（1）', '武汉分公司 · 收货专用章（2）',
    '兰州分公司 · 业务章（3）', '公章',
  ]);
  assert.deepEqual(group.items.map(item => item.standard.id), [7, 8, 9, 10]);
  assert.equal(group.items[0].standard, standards[0]);
  assert.equal(group.items[0].description.company, company);
  assert.equal(group.items[0].description.branch, '兰州分公司');
});

test('a named maintenance center groups business stamps without losing terminal numbers', () => {
  const groups = groupStandards([
    {id: 1, requirement: '北京集中维修中心业务章（3）'},
    {id: 2, requirement: '北京集中维修中心业务章（1）'},
    {id: 3, requirement: '北京集中维修中心收货专用章②'},
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, '北京集中维修中心');
  assert.deepEqual(groups[0].items.map(item => item.description.seal), ['业务章（3）', '业务章（1）', '收货专用章②']);
});

test('warehouse and city-only branches retain their location within the exact legal owner', () => {
  const groups = groupStandards([
    {requirement: '深圳欧瑞特供应链管理有限公司福州仓库收货专用章（1）'},
    {requirement: '深圳欧瑞特供应链管理有限公司兰州分公司收货专用章（1）'},
    {requirement: '重庆伟仕通供应链管理有限公司上海收货专用章'},
    {requirement: '重庆伟仕通供应链管理有限公司北京收货专用章'},
    {requirement: '济南新宇航科技发展有限公司济宁分公司'},
    {requirement: '济南新宇航科技发展有限公司业务专用章'},
  ]);
  assert.equal(groups.length, 3);
  assert.deepEqual(groups[0].items.map(item => item.description.label), ['福州仓库 · 收货专用章（1）', '兰州分公司 · 收货专用章（1）']);
  assert.deepEqual(groups[1].items.map(item => item.description.label), ['上海 · 收货专用章', '北京 · 收货专用章']);
  assert.deepEqual(groups[2].items.map(item => item.description.label), ['济宁分公司', '业务专用章']);
});

test('legacy responses without parsed metadata keep generic center identities separate', () => {
  const requirements = [
    '三星电子维修中心5972036', '三星电子维修中心2310637',
    '三星电子服务中心授权代码:6055487', '三星电子服务中心授权代码:4910964',
    '三星电子服务中心取机专用章5785258站', '三星电子服务中心取机专用章1234567站',
    '三星电子维修中心业务章（1）', '三星电子维修中心业务章（2）',
  ];
  const groups = groupStandards(requirements.map(requirement => ({requirement})));
  assert.equal(groups.length, requirements.length);
  assert.deepEqual(groups.map(group => group.title), requirements);
});

test('similar names and warehouse codes do not merge across distinct owners', () => {
  const groups = groupStandards([
    {requirement: '深圳欧瑞特供应链管理有限公司兰州分公司收货章'},
    {requirement: '深圳欧瑞特供应链服务有限公司兰州分公司收货章'},
    {requirement: '深圳欧瑞特供应链管理股份有限公司兰州分公司收货章'},
    {requirement: '广州花东仓O20DCH收货专用章①/②'},
    {requirement: '广州花东仓O20DCM收货专用章①/②'},
    {requirement: '北京集中维修中心业务章（3）'},
    {requirement: '北京集中维修服务中心业务章（3）'},
  ]);
  assert.equal(groups.length, 7);
  assert.equal(describeStandard({requirement: '广州花东仓O20DCH收货专用章①/②'}).seal, '收货专用章①/②');
});

test('the reviewed requirement wins over contradictory and more detailed recognized text', () => {
  const company = '深圳欧瑞特供应链管理有限公司';
  const groups = groupStandards([
    {id: 1, requirement: `${company}兰州分公司收货章`, recognized: '完全不同有限公司公章'},
    {id: 2, requirement: `${company}武汉分公司收货章`, recognized: '错误的识别结果'},
    {id: 3, requirement: '客户章（1）', recognized: `${company}兰州分公司收货章`},
    {id: 4, requirement: '客户章（2）', recognized: `${company}兰州分公司收货章`},
    {id: 5, requirement: '', recognized: `${company}兰州分公司收货章`},
    {id: 6, requirement: '', recognized: `${company}兰州分公司收货章`},
  ]);
  assert.equal(groups.length, 5);
  assert.equal(groups[0].title, company);
  assert.deepEqual(groups[0].items.map(item => item.standard.id), [1, 2]);
  assert.equal(groups[1].title, '客户章（1）');
  assert.equal(groups[2].title, '客户章（2）');
});

test('group order follows the newest encountered item and member order stays unchanged', () => {
  const standards = [
    {id: 8, requirement: '北京集中维修中心业务章（3）'},
    {id: 7, requirement: '深圳欧瑞特供应链管理有限公司兰州分公司收货章'},
    {id: 6, requirement: '北京集中维修中心业务章（1）'},
    {id: 5, requirement: '深圳欧瑞特供应链管理有限公司武汉分公司收货章'},
    {id: 4, requirement: '独立签章要求'},
  ];
  const groups = groupStandards(standards);
  assert.deepEqual(groups.map(group => group.items.map(item => item.standard.id)), [[8, 6], [7, 5], [4]]);
  assert.deepEqual(standards.map(item => item.id), [8, 7, 6, 5, 4]);
});

test('singleton labels preserve the complete requirement and grouped labels retain all identifying suffixes', () => {
  const requirement = '深圳欧瑞特供应链管理有限公司兰州分公司收货专用章（1）';
  assert.equal(groupStandards([{requirement}])[0].items[0].description.label, requirement);
  assert.equal(describeStandard({requirement}).label, '兰州分公司 · 收货专用章（1）');
  assert.equal(describeStandard({requirement: '广州花东仓O20DCH收货专用章①/②'}).label, '收货专用章①/②');
});

test('explicit whitespace and fullwidth branch delimiters are harmless without removing branch distinctions', () => {
  const groups = groupStandards([
    {requirement: '深圳 欧瑞特供应链管理有限公司 （兰州分公司） 收货专用章（１）'},
    {requirement: '深圳欧瑞特供应链管理有限公司武汉分公司收货专用章(2)'},
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].items[0].description.branch, '兰州分公司');
  assert.equal(groups[0].items[0].description.seal, '收货专用章（１）');
});

test('ambiguous or unsupported requirements stay intact instead of being guessed into a company', () => {
  for (const requirement of [
    '业务章（1）', '业务章（2）', '客户指定章', '甲公司或乙公司收货章',
    '深圳欧瑞特供应链管理有限公司及指定代理收货章',
  ]) {
    const description = describeStandard({requirement});
    assert.equal(description.company, '', requirement);
    assert.equal(description.label, requirement, requirement);
  }
  assert.deepEqual(groupStandards([]), []);
  assert.equal(groupStandards([{recognized: '甲公司公章'}, {recognized: '甲公司公章'}]).length, 2);
});
