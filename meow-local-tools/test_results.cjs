const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'web/app.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf("\ndocument.querySelectorAll('[data-mode]')")), context);
const { latestResults, resultTime } = vm.runInContext('({latestResults,resultTime})', context);
const row = (id, status='complete') => ({provider_id:id, name:id, status, valid:32, score:.8});
const record = (id, day, rows, mode='gpt') => ({id, created_at:`2026-09-${day}T01:00:00Z`, rows, mode,
  request_model:'request-'+id, claimed_model:'claimed-'+id, benchmark:{version:'baseline-'+id}, tier:'low'});
const old = record('old', '23', [row('a'),row('b'),row('c')]);
const recent = record('recent', '24', [row('b','failed')]);

test('a single-provider rerun preserves other providers and their own metadata', () => {
  const results = latestResults([old,recent], 'gpt');
  assert.equal(results.size,3);
  assert.equal(results.get('a').batch.id,'old');
  assert.equal(results.get('b').batch.id,'recent');
  assert.equal(results.get('b').row.status,'failed');
  assert.equal(resultTime(results.get('c')),old.created_at);
});
test('queued or cancelled tasks do not erase prior results after interruption', () => {
  const skipped=record('skipped','25',[row('a','cancelled'),row('c','queued')]);
  const results=latestResults([skipped,recent,old],'gpt');
  assert.equal(results.get('a').batch.id,'old');
  assert.equal(results.get('c').batch.id,'old');
});
test('active progress replaces only providers in the running batch', () => {
  const live=record('live','25',[row('a','running'),row('c','queued')]);
  const results=latestResults([live,recent,old],'gpt','live');
  assert.equal(results.get('a').row.status,'running');
  assert.equal(results.get('c').row.status,'queued');
  assert.equal(resultTime(results.get('c')),null);
  assert.equal(results.get('b').batch.id,'recent');
});
test('families stay isolated and old records default to OpenAI', () => {
  const legacy={...old}; delete legacy.mode;
  const claude=record('claude','25',[row('a')],'claude');
  assert.equal(latestResults([claude,legacy],'gpt').get('a').batch.id,'old');
  assert.equal(latestResults([claude,legacy],'claude').size,1);
});
test('live snapshot wins over its saved copy with the same timestamp', () => {
  const live={...recent,rows:[row('b','running')]};
  assert.equal(latestResults([live,recent],'gpt','recent').get('b').row.status,'running');
});
test('dashboard exports each provider once while history retains its own batch', () => {
  context.fixture={config:{providers:['a','b','c'].map(id=>({id}))},history:[recent,old],current:null,busy:false};
  vm.runInContext("state=fixture;mode='gpt';view='desk';batchId=null",context);
  const exported=JSON.parse(vm.runInContext('JSON.stringify(summaryRows())',context));
  assert.equal(exported.length,3);
  assert.equal(exported[0][10],'request-old');
  assert.equal(exported[1][10],'request-recent');
  vm.runInContext("view='history';batchId='old'",context);
  const historic=JSON.parse(vm.runInContext('JSON.stringify(summaryRows())',context));
  assert.equal(historic.length,3);
  assert.ok(historic.every(r=>r[10]==='request-old'));
  assert.equal(historic[1][1],'已完成');
});
