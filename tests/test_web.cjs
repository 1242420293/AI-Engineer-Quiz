// Run with node --test tests/test_web.cjs; no npm dependencies required.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(fetch) {
  const elements = new Map();
  function element(selector) {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, { value: '', innerHTML: '', textContent: '', disabled: false,
        handlers: {}, classList: { add: c => classes.add(c), remove: c => classes.delete(c),
          toggle: (c, on) => on ? classes.add(c) : classes.delete(c), contains: c => classes.has(c) },
        addEventListener(event, fn) { this.handlers[event] = fn; }, setAttribute() {} });
    }
    return elements.get(selector);
  }
  const timers = new Map(); let timer = 0;
  const context = vm.createContext({
    document: { querySelector: element, querySelectorAll: () => [], body: element('body') },
    localStorage: { getItem: () => null, removeItem() {}, setItem() {} },
    fetch, URLSearchParams, URL, setTimeout(fn) { timers.set(++timer, fn); return timer; },
    clearTimeout(id) { timers.delete(id); }, console,
  });
  const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8').split('// ---------- boot ----------')[0];
  vm.runInContext(source + '\nglobalThis.subject = {api, state, loadLibrary, filtersChanged, queryParams, openAsset};', context);
  context.subject.state.token = 'test-token';
  return { ...context.subject, element, timers };
}
const reply = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: {'content-type':'application/json'} });
const results = title => ({ total: 1, items: [{uuid: title, title, media_type:'image'}], facets: {} });

test('JSON defaults to POST, keeps PATCH; GET and CSV retain bearer auth', async () => {
  const calls = [];
  const h = harness(async (url, options) => { calls.push([url, options]); return reply({ok:true}); });
  await h.api('/api/auth/login', {json:{username:'admin',password:'admin'}});
  await h.api('/api/assets/one', {method:'PATCH',json:{title:'Edited'}});
  await h.api('/api/assets/export.csv');
  assert.equal(calls[0][1].method, 'POST');
  assert.equal(JSON.parse(calls[0][1].body).username, 'admin');
  assert.equal(calls[1][1].method, 'PATCH');
  assert.equal(calls[2][1].method, undefined);
  assert.equal(calls[2][1].headers.Authorization, 'Bearer test-token');
});

test('late older response cannot overwrite the latest search', async () => {
  const pending = [];
  const h = harness(() => new Promise(resolve => pending.push(resolve)));
  h.state.q = 'old'; const oldRequest = h.loadLibrary();
  h.state.q = 'latest'; const newRequest = h.loadLibrary();
  pending[1](reply(results('LATEST'))); await newRequest;
  pending[0](reply(results('OLD'))); await oldRequest;
  assert.match(h.element('#grid').innerHTML, /LATEST/);
  assert.doesNotMatch(h.element('#grid').innerHTML, /OLD/);
  assert.equal(h.state.loading, false);
});

test('typing invalidates in-flight requests before the debounce fires and resets the page', async () => {
  let resolve;
  const h = harness(() => new Promise(done => resolve = done));
  h.state.offset = 20; const old = h.loadLibrary();
  h.state.q = 'new'; h.filtersChanged(true);
  resolve(reply(results('STALE'))); await old;
  assert.equal(h.state.offset, 0);
  assert.doesNotMatch(h.element('#grid').innerHTML, /STALE/);
  assert.equal(h.state.loading, true);
  assert.equal(h.element('#export-csv').disabled, true);
});

test('CSV gets the same complete filters without pagination; errors are visible', async () => {
  const h = harness(async () => reply({detail:'server failure'}, 500));
  Object.assign(h.state, {q:'harbor', collection:'3', media:'image', include:['a,b','campaign'], exclude:['internal'], sort:'oldest', offset:20});
  const p = h.queryParams();
  assert.deepEqual(p.getAll('include_tag'), ['a,b','campaign']);
  assert.equal(p.get('exclude_tag'), 'internal');
  assert.equal(p.get('collection_id'), '3');
  assert.equal(p.get('media_type'), 'image');
  assert.equal(p.get('sort'), 'oldest');
  assert.equal(p.has('offset'), false);
  await h.loadLibrary();
  assert.equal(h.element('#query-error').classList.contains('hidden'), false);
  assert.match(h.element('#query-error-message').textContent, /server failure/);
  assert.equal(h.element('#page-next').disabled, true);
  await h.element('#export-csv').handlers.click();
  assert.match(h.element('#toast').textContent, /CSV download failed/);
});

function drawerReply(url) {
  if (url.endsWith('/metadata')) return reply({});
  if (url.endsWith('/similar')) return reply({items:[]});
  if (url === '/api/tags') return reply({tags:[]});
  if (url === '/api/collections') return reply({collections:[]});
  if (url.startsWith('/api/assets?')) return reply(results('test'));
  return reply({asset:{id:1,uuid:'test',title:'Test',media_type:'image'}, tags:[],versions:[]});
}

test('drawer tag form submits JSON through POST and reports success', async () => {
  const calls = [];
  const h = harness(async (url, options) => {
    if (url.endsWith('/tags') && options.method === 'POST') {
      calls.push([url, options]); return reply({ok:true});
    }
    return drawerReply(url);
  });
  h.state.user = {role:'admin'};
  await h.openAsset('test');
  assert.match(h.element('#drawer-body').innerHTML, /type="submit">Add tag/);
  h.element('#tag-add').value = ' manualtag1124 ';
  let prevented = false;
  await h.element('#tag-add-form').handlers.submit({preventDefault(){ prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/assets/test/tags');
  assert.equal(JSON.parse(calls[0][1].body).name, 'manualtag1124');
  assert.equal(h.element('#toast').textContent, 'Tag added');
});

test('drawer tag form rejects blanks and keeps failed input with an error message', async () => {
  let posts = 0;
  const h = harness(async (url, options) => {
    if (options.method === 'POST') { posts++; return reply({detail:'unavailable'}, 500); }
    return drawerReply(url);
  });
  h.state.user = {role:'admin'};
  await h.openAsset('test');
  const submit = () => h.element('#tag-add-form').handlers.submit({preventDefault(){}});
  h.element('#tag-add').value = '   ';
  await submit(); assert.equal(posts, 0);
  h.element('#tag-add').value = 'manualtag1124';
  await submit();
  assert.equal(posts, 1);
  assert.equal(h.element('#tag-add').value, 'manualtag1124');
  assert.match(h.element('#tag-error').textContent, /Could not add tag.*unavailable/);
  assert.equal(h.element('#tag-error').classList.contains('hidden'), false);
  assert.equal(h.element('#tag-add-submit').disabled, false);
});
