/** Exercise the real navigation modules without launching a browser. */
import assert from 'node:assert/strict';

class NodeStub extends EventTarget {
  constructor() {
    super();
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.hidden = false;
    this.clientWidth = 1200;
    this.clientHeight = 800;
    this.scrollTop = 0;
    this.naturalWidth = 640;
    this.naturalHeight = 400;
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  setAttribute(name, value) {
    if (name === 'id') nodes.set(`#${value}`, this);
  }
  focus() {}
  contains() { return false; }
  append(...nodes) { this.children.push(...nodes); }
  insertBefore(node) { this.children.push(node); }
  replaceChildren(...nodes) { this.children = nodes; }
  remove() {}
  removeAttribute() {}
  querySelector() { return new NodeStub(); }
  setPointerCapture() {}
  set src(value) { this._src = value; queueMicrotask(() => this.onload?.()); }
  get src() { return this._src; }
}
const nodes = new Map();
globalThis.document = {
  body: new NodeStub(),
  querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, new NodeStub());
    return nodes.get(selector);
  },
  createElement() { return new NodeStub(); },
};
globalThis.window = new EventTarget();
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.location = {pathname: '/', hash: '', search: ''};
globalThis.history = {replaceState(_a, _b, hash) { location.hash = hash.slice(1); }, pushState() {}};
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
globalThis.Image = NodeStub;
globalThis.ResizeObserver = class { observe() {} };
let requests = [];
let rows = Array.from({length: 32}, (_, i) => ({id: i + 1, name: `img${i + 1}.jpg`, ext: 'jpg',
  mtime: 5000 - i, width: 640, height: 400, rating: 0, favorite: 0, meta_state: 1}));
globalThis.fetch = async input => {
  const url = new URL(input, 'http://127.0.0.1');
  requests.push(url);
  if (url.pathname.endsWith('/preview')) return new Response(new Blob(['image']), {status: 200});
  if (url.pathname.endsWith('/position')) {
    return Response.json({index: rows.findIndex(row => row.id === Number(url.searchParams.get('image_id')))});
  }
  if (/\/api\/images\/\d+$/.test(url.pathname)) {
    return Response.json(rows.find(row => row.id === Number(url.pathname.split('/').at(-1))));
  }
  assert.ok(url.pathname.endsWith('/images'), url.pathname);
  const offset = Number(url.searchParams.get('offset') || 0);
  const limit = Number(url.searchParams.get('limit') || 200);
  return Response.json({total: rows.length, offset, items: rows.slice(offset, offset + limit)});
};

const {state, query, readHash, sortValue} = await import('../photo_explorer/static/js/state.js');
const {refreshGrid, itemAt} = await import('../photo_explorer/static/js/grid.js');
const {navigate, zoomBy, applyMode} = await import('../photo_explorer/static/js/viewer.js');
state.root = 1;
state.total = rows.length;
state.view = 'viewer';
state.sort = 'mtime';
state.order = 'desc';
state.filters = {ext: 'jpg', q: 'img'};
await refreshGrid();
assert.equal((await itemAt(5)).id, 6);
for (let index = 5; index < rows.length; index++) {
  await navigate(index);
  assert.equal(state.current.id, rows[index].id);
  assert.equal(state.index, index);
}
assert.ok(requests.filter(url => url.pathname.endsWith('/images')).every(url =>
  url.searchParams.get('sort') === 'mtime' && url.searchParams.get('order') === 'desc' &&
  url.searchParams.get('ext') === 'jpg' && url.searchParams.get('q') === 'img'));
await navigate(5);
rows.splice(5, 1);
await navigate(5);
assert.equal(state.current.id, 7);
assert.equal(state.total, 31);
await navigate(999);
assert.equal(state.index, 30);
state.prefs.wrap = true;
await navigate(1, true);
assert.equal(state.index, 0);
applyMode('actual');
zoomBy(1.25, 100, 100);
assert.ok(document.querySelector('#view-image').style.transform.includes('scale(1.25)'));
location.hash = '#root=7&sort=name&order=asc&ext=png&view=viewer&i=17';
readHash();
assert.equal(state.root, 7);
assert.equal(state.index, 17);
assert.equal(query(), 'sort=name&order=asc&ext=png');
assert.equal(sortValue({name: 'a.png'}), 'a.png');
console.log('Frontend contract: ordered navigation, filter parity, deletion clamp, wrap, zoom, hash restore passed.');

const {buildLayout, indexPosition, visibleWindow} = await import('../photo_explorer/static/js/layout.js');
const grouped = buildLayout(50000, 6, 220, [
  {start: 0, count: 7, date: '2026-10-01'}, {start: 7, count: 49993, date: '2026-09-30'},
]);
assert.equal(indexPosition(grouped, 0).y, 42);
assert.equal(indexPosition(grouped, 6).y, 262);
assert.equal(indexPosition(grouped, 7).y, 516);
assert.equal(indexPosition(grouped, 7).x, 0);
const visible = visibleWindow(grouped, 100000, 900);
assert.ok(visible.indices.length <= 60, `Too many virtual cells: ${visible.indices.length}`);
assert.equal(visible.headers.length, 1);
assert.equal(grouped.height, 8 + 34 + 440 + 34 + Math.ceil(49993 / 6) * 220 + 8);
const resized = buildLayout(50000, 3, 180, [{start: 0, count: 50000, date: '2026-10-01'}]);
assert.ok(visibleWindow(resized, 200000, 900).indices.length <= 33);

state.root = 1;
state.sort = 'mtime';
state.order = 'desc';
state.filters = {};
state.index = 5;
state.total = rows.length;
state.view = 'viewer';
state.prefs.slideInterval = 1;
const {slideshow, toggleSlideshow, stopSlideshow} = await import('../photo_explorer/static/js/playback.js');
let scheduled;
globalThis.requestAnimationFrame = callback => { scheduled = callback; return 1; };
toggleSlideshow();
assert.equal(slideshow.active, true);
await scheduled(performance.now() + 1100);
assert.equal(state.index, 6);
toggleSlideshow();
assert.equal(slideshow.paused, true);
await scheduled(performance.now() + 3000);
assert.equal(state.index, 6);
stopSlideshow();
state.index = state.total - 1;
state.prefs.slideshowLoop = false;
toggleSlideshow();
await scheduled(performance.now() + 1100);
assert.equal(slideshow.active, false);
state.prefs.slideshowLoop = true;
toggleSlideshow();
await scheduled(performance.now() + 1100);
assert.equal(state.index, 0);
stopSlideshow();
console.log('Virtualization contract: 50,000-item grouped virtualization, resize, slideshow ordering, pause, end-stop and loop passed.');

const {initCompare, openCompare, closeCompare, compareZoom, comparing} = await import('../photo_explorer/static/js/compare.js');
initCompare();
state.selected = new Set([4, 2]);
await openCompare();
assert.equal(comparing(), true);
const comparePanes = document.querySelector('#compare-panes').children;
assert.equal(comparePanes.length, 2);
assert.equal(comparePanes[0].children[0].children[0].textContent, 'img2.jpg');
compareZoom(2);
const compareImages = comparePanes.map(pane => pane.children[1].children[0]);
assert.ok(compareImages.every(image => image.style.transform.includes('scale(2)')));
const compareStage = comparePanes[0].children[1];
function pointer(type, x, y) {
  const event = new Event(type);
  Object.assign(event, {clientX: x, clientY: y, pointerId: 1});
  compareStage.dispatchEvent(event);
}
pointer('pointerdown', 200, 200);
pointer('pointermove', 320, 280);
assert.ok(compareImages.every(image => image.style.transform.includes('120px') && image.style.transform.includes('80px')));
pointer('pointerup', 320, 280);
closeCompare();
assert.equal(comparing(), false);
console.log('Comparison contract: comparison pane order, synchronized zoom and pan, and exit passed.');

let keyHandler;
document.addEventListener = (type, callback) => { if (type === 'keydown') keyHandler = callback; };
const {initKeys} = await import('../photo_explorer/static/js/keys.js');
initKeys();
document.querySelector('#context-menu').hidden = true;
state.view = 'grid';
let opened = 0;
window.addEventListener('open-viewer', () => { opened++; });
let prevented = false;
await keyHandler({key: 'Enter', target: {closest: selector => selector === 'button,summary,a' ? {} : null},
  preventDefault() { prevented = true; }});
assert.equal(prevented, false, 'Enter on a button must retain native activation');
assert.equal(opened, 0);
await keyHandler({key: 'Enter', target: {closest: () => null}, preventDefault() { prevented = true; }});
assert.equal(opened, 1, 'Enter on the grid must open the viewer');
assert.equal(prevented, true);
console.log('Keyboard contract: native button activation and grid Enter behavior passed.');

const {newestUndoable, structureOptions, serializeCondition, serializeRule} = await import('../photo_explorer/static/js/operation_logic.js');
assert.equal(newestUndoable([{id: 4, status: 'running', undoable: 1}, {id: 3, status: 'done', undoable: 1, undone_by: 8},
  {id: 2, status: 'done', undoable: 1}, {id: 1, status: 'done', undoable: 1}]).id, 2);
assert.equal(newestUndoable([{id: 1, status: 'done', undoable: 0}]), null);
const structure = new URLSearchParams(structureOptions('E:\\Photos & Trips', false, 'skip, E:\\A+B'));
assert.equal(structure.get('path'), 'E:\\Photos & Trips');
assert.equal(structure.get('files'), '0');
assert.equal(structure.get('skip'), 'skip, E:\\A+B');
assert.deepEqual(serializeCondition('size', 'between', '1', 'MB', '5'), {field: 'size', op: 'between', value: [1048576, 5242880]});
assert.equal(serializeCondition('size', 'lt', '100', 'KB').value, 102400);
assert.deepEqual(serializeCondition('ext', 'in', '.PNG; jpg').value, ['png', 'jpg']);
assert.equal(serializeCondition('favorite', 'is', 'false').value, false);
assert.throws(() => serializeCondition('size', 'gt', 'abc'));
const rule = serializeRule({name: 'test', root: 1, dir: null, dirMode: 'exact', ids: [3, 5], others: false, match: 'any',
  conditions: [serializeCondition('name', 'suffix', '-t')], type: 'move', dest: 'E:\\Temp', collision: 'skip', createDest: true});
assert.deepEqual(rule.scope, {root_id: 1, dir: null, dir_mode: 'exact', include_other_files: false, ids: [3, 5]});
assert.equal(rule.action.create_dest, true);
console.log('Frontend contract: undo choice, structure encoding, typed rule values and scope serialization passed.');
