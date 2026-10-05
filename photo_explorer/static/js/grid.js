/** A row-window virtual grid backed by bounded server page caches. */
import {$, el, api, dispatch, on} from './api.js';
import {state, listURL, sortValue, savePrefs, query} from './state.js';
import {buildLayout, indexPosition, visibleWindow} from './layout.js';

const pageSize = 200;
let pages = new Map();
let pending = new Map();
let generation = 0;
let columns = 1;
let cellWidth = 200;
let rowHeight = 228;
let anchor = 0;
let renderFrame = 0;
let groups = [];
let layout = buildLayout(0, 1, 228);
const grid = $('#grid');
const space = $('#grid-space');

export function getColumns() { return columns; }
export function getRowHeight() { return rowHeight; }
export function cachedItem(index) { return pages.get(Math.floor(index / pageSize))?.[index % pageSize]; }

async function loadPage(page) {
  if (pages.has(page)) return pages.get(page);
  if (pending.has(page)) return pending.get(page);
  if (!state.root) return [];
  const token = generation;
  const promise = api(listURL({offset: page * pageSize, limit: pageSize})).then(result => {
    if (token !== generation) return [];
    state.total = result.total;
    pages.set(page, result.items);
    while (pages.size > 18) pages.delete(pages.keys().next().value);
    schedule();
    dispatch('total-changed');
    return result.items;
  }).finally(() => { if (token === generation) pending.delete(page); });
  pending.set(page, promise);
  return promise;
}

export async function itemAt(index) {
  if (index < 0 || index >= state.total) return null;
  const items = await loadPage(Math.floor(index / pageSize));
  return items[index % pageSize] || null;
}

export async function refreshGrid(resetScroll = false) {
  const token = ++generation;
  pages = new Map();
  pending = new Map();
  if (resetScroll) grid.scrollTop = 0;
  if (!state.root) { state.total = 0; render(); return; }
  const firstVisible = visibleWindow(layout, grid.scrollTop, grid.clientHeight).indices[0] || 0;
  const page = Math.floor(firstVisible / pageSize);
  const groupRequest = state.prefs.groupDates && ['mtime', 'ctime', 'taken'].includes(state.sort)
    ? api(`/api/roots/${state.root}/groups?${query()}`) : Promise.resolve([]);
  const [, newGroups] = await Promise.all([loadPage(page), groupRequest]);
  if (token !== generation) return;
  groups = newGroups;
  measure();
  if (state.total && page * pageSize >= state.total) {
    grid.scrollTop = indexPosition(layout, state.total - 1).y;
    await loadPage(Math.floor((state.total - 1) / pageSize));
  }
  schedule();
}

function measure() {
  const width = Math.max(120, grid.clientWidth - 16);
  columns = Math.max(1, Math.floor(width / Number(state.prefs.thumbSize)));
  cellWidth = Math.floor(width / columns);
  rowHeight = cellWidth + 27;
  layout = buildLayout(state.total, columns, rowHeight, groups);
}

function schedule() {
  if (renderFrame) return;
  renderFrame = requestAnimationFrame(() => { renderFrame = 0; render(); });
}

function render() {
  measure();
  space.style.height = `${layout.height}px`;
  grid.setAttribute('aria-rowcount', Math.ceil(state.total / columns));
  grid.setAttribute('aria-colcount', columns);
  $('#empty-state').hidden = state.total > 0;
  if (!state.total && state.root) {
    $('#empty-state h1').textContent = 'No images in this view';
    $('#empty-state p').textContent = 'The folder may still be scanning, or a filter may exclude its images.';
  } else if (!state.root) {
    $('#empty-state h1').textContent = 'A place for every photo.';
    $('#empty-state p').textContent = 'Open a folder, choose an order, and explore.';
  }
  const {indices, headers} = visibleWindow(layout, grid.scrollTop, grid.clientHeight);
  const wanted = new Set(indices);
  const existing = new Map([...space.querySelectorAll('.cell')].map(node => [Number(node.dataset.index), node]));
  for (const [index, node] of existing) if (!wanted.has(index)) { node.remove(); existing.delete(index); }
  for (const node of space.querySelectorAll('.group-header')) node.remove();
  for (const group of headers) {
    const header = el('div', {class: 'group-header', role: 'row'}, `${group.date} · ${group.count.toLocaleString()} images`);
    header.style.top = `${Math.min(group.endY - 34, Math.max(group.y, grid.scrollTop))}px`;
    space.append(header);
  }
  for (const index of indices) {
    const item = cachedItem(index);
    let cell = existing.get(index);
    const signature = item ? `${item.id}:${item.size}:${item.mtime}:${item.meta_state}:${item.rating}:${item.favorite}:${state.sort}:${state.prefs.thumbSize}` : 'pending';
    if (!cell || cell.dataset.signature !== signature) {
      cell?.remove();
      cell = createCell(item, index);
      cell.dataset.signature = signature;
      space.append(cell);
    }
    cell.style.width = `${cellWidth}px`;
    cell.style.height = `${rowHeight}px`;
    const position = indexPosition(layout, index);
    cell.style.left = `${8 + position.x * cellWidth}px`;
    cell.style.top = `${position.y}px`;
    cell.classList.toggle('selected', !!item && state.selected.has(item.id));
    cell.classList.toggle('focused', index === state.focusIndex);
    cell.setAttribute('aria-selected', String(!!item && state.selected.has(item.id)));
    if (!item) loadPage(Math.floor(index / pageSize)).catch(() => {});
  }
  selectionStatus();
}

function createCell(item, index) {
  const cell = el('div', {class: `cell${item ? '' : ' skeleton'}${item?.meta_state === 2 ? ' failed' : ''}`,
    role: 'gridcell', 'data-index': index, 'aria-rowindex': Math.floor(index / columns) + 1,
    'aria-colindex': index % columns + 1});
  if (!item) return cell;
  cell.setAttribute('aria-label', `${item.name}. ${sortValue(item, index)}`);
  cell.title = `${item.name}\n${sortValue(item, index)}${item.meta_error ? `\n${item.meta_error}` : ''}`;
  const size = state.prefs.thumbSize > 256 ? 400 : state.prefs.thumbSize <= 160 ? 160 : 256;
  const image = el('img', {src: `/api/images/${item.id}/thumb?s=${size}&v=${item.mtime}-${item.size}`, alt: item.name,
    loading: 'lazy', decoding: 'async', draggable: 'false'});
  image.onerror = () => { image.alt = `${item.name} — unavailable`; cell.classList.add('failed'); };
  cell.append(image, el('span', {class: 'caption'}, item.name), el('span', {class: 'sort-value'}, sortValue(item, index)));
  const badges = el('span', {class: 'badges'});
  if (item.animated) badges.append(el('span', {class: 'badge'}, `${item.ext.toUpperCase()} · ${item.frames || '?'} frames`));
  if (item.rating) badges.append(el('span', {class: 'badge'}, `★ ${item.rating}`));
  if (item.favorite) badges.append(el('span', {class: 'badge'}, '♥'));
  if (['heic', 'heif', 'tif', 'tiff', 'psd'].includes(item.ext)) badges.append(el('span', {class: 'badge'}, `${item.ext.toUpperCase()} rendered`));
  cell.append(badges);
  cell.onclick = event => { selectIndex(index, event).catch(() => {}); grid.focus({preventScroll: true}); };
  cell.ondblclick = () => dispatch('open-viewer', {index});
  cell.oncontextmenu = event => {
    event.preventDefault();
    if (!state.selected.has(item.id)) { state.selected.clear(); state.selected.add(item.id); }
    state.focusIndex = index;
    schedule();
    dispatch('context-menu', {x: event.clientX, y: event.clientY, item});
  };
  return cell;
}

export async function selectIndex(index, modifiers = {}) {
  index = Math.max(0, Math.min(state.total - 1, index));
  const item = await itemAt(index);
  if (!item) return;
  if (modifiers.shiftKey) {
    if (!modifiers.ctrlKey) state.selected.clear();
    for (let i = Math.min(anchor, index); i <= Math.max(anchor, index); i++) {
      const current = await itemAt(i);
      if (current) state.selected.add(current.id);
    }
  } else if (modifiers.ctrlKey || modifiers.metaKey) {
    if (state.selected.has(item.id)) state.selected.delete(item.id);
    else state.selected.add(item.id);
    anchor = index;
  } else {
    state.selected.clear();
    state.selected.add(item.id);
    anchor = index;
  }
  state.focusIndex = index;
  schedule();
}

export async function selectAll() {
  const revision = state.revision;
  for (let offset = 0; offset < state.total; offset += 500) {
    const result = await api(listURL({offset, limit: 500}));
    if (revision !== state.revision) return;
    result.items.forEach(item => state.selected.add(item.id));
  }
  schedule();
}

export async function scrollToIndex(index, select = false) {
  measure();
  index = Math.max(0, Math.min(index, state.total - 1));
  const top = indexPosition(layout, index).y;
  if (top < grid.scrollTop || top + rowHeight > grid.scrollTop + grid.clientHeight) {
    grid.scrollTop = Math.max(0, top - (grid.clientHeight - rowHeight) / 2);
  }
  if (select) await selectIndex(index);
  schedule();
}

export function selectionStatus() {
  const count = state.selected.size;
  $('#selection-toolbar').hidden = !count || state.view === 'viewer';
  $('#selection-count').textContent = `${count.toLocaleString()} selected`;
  $('#selection-status').textContent = count ? `${count.toLocaleString()} selected` : '';
}

export function initGrid() {
  grid.addEventListener('scroll', schedule, {passive: true});
  new ResizeObserver(schedule).observe(grid);
  $('#thumb-size').value = state.prefs.thumbSize;
  $('#thumb-size').oninput = event => savePrefs({thumbSize: Number(event.target.value)});
  $('#clear-selection').onclick = () => { state.selected.clear(); schedule(); };
  on('prefs-changed', () => { pages = new Map(pages); schedule(); });
  on('selection-changed', schedule);
}
