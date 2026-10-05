/** Server-indexed navigation, bounded preview prefetch, and viewport transforms. */
import {$, api, dispatch, el, on, dateText, sizeText, toast} from './api.js';
import {state, listURL, sortValue, writeHash, savePrefs, query} from './state.js';
import {refreshGrid, scrollToIndex, selectionStatus} from './grid.js';

const viewer = $('#viewer');
const stage = $('#image-stage');
const image = $('#view-image');
let navigation = 0;
let scale = 1;
let mode = 'fit';
let pan = {x: 0, y: 0};
let dimensions = {width: 1, height: 1};
let hideTimer;
let previewCache = new Map();
let original = false;
let dragging = null;
let retainedURL = null;
let rotation = 0;
let flipX = 1;
let flipY = 1;
const pointers = new Map();
let pinch = null;
let touchStart = null;
const deferredRevoke = new Set();

function backgroundInert(value) {
  for (const id of ['topbar', 'locationbar', 'workspace', 'statusbar', 'selection-toolbar']) {
    $(`#${id}`).inert = value;
  }
}

async function previewURL(item) {
  const key = `${item.id}:${item.size}:${item.mtime}`;
  if (!previewCache.has(key)) {
    const promise = fetch(`/api/images/${item.id}/preview?v=${item.mtime}-${item.size}`).then(async response => {
      if (!response.ok) {
        const error = await response.json().catch(() => ({detail: 'Preview unavailable'}));
        const failure = new Error(error.detail);
        failure.status = response.status;
        throw failure;
      }
      return URL.createObjectURL(await response.blob());
    });
    previewCache.set(key, promise);
    promise.catch(() => { if (previewCache.get(key) === promise) previewCache.delete(key); });
  }
  const promise = previewCache.get(key);
  previewCache.delete(key);
  previewCache.set(key, promise);
  while (previewCache.size > 12) {
    const oldest = previewCache.keys().next().value;
    const removed = previewCache.get(oldest);
    previewCache.delete(oldest);
    removed.then(url => {
      if (url !== retainedURL) URL.revokeObjectURL(url);
      else deferredRevoke.add(url);
    }).catch(() => {});
  }
  return promise;
}

export function showOverlays() {
  viewer.classList.remove('hide-overlays');
  clearTimeout(hideTimer);
  if (!state.prefs.pinOverlays) hideTimer = setTimeout(() => {
    if (!viewer.contains(document.activeElement) || document.activeElement === viewer) viewer.classList.add('hide-overlays');
  }, 2500);
}

export async function openViewer(index, push = true) {
  if (!state.total) return;
  const wasOpen = state.view === 'viewer' && !viewer.hidden;
  state.view = 'viewer';
  viewer.hidden = false;
  backgroundInert(true);
  viewer.focus();
  selectionStatus();
  state.index = index;
  writeHash(push && !wasOpen);
  showOverlays();
  await navigate(index);
}

export async function closeViewer(updateHash = true) {
  navigation++;
  state.view = 'grid';
  viewer.hidden = true;
  backgroundInert(false);
  clearTimeout(hideTimer);
  dispatch('viewer-closed');
  if (updateHash) writeHash();
  await refreshGrid();
  await scrollToIndex(state.index, true);
  $('#grid').focus({preventScroll: true});
}

export async function navigate(requested, relative = false) {
  if (!state.root || state.view !== 'viewer') return;
  const token = ++navigation;
  let index = relative ? state.index + requested : requested;
  if (index < 0 || index >= state.total) {
    if (state.prefs.wrap && state.total) index = (index + state.total) % state.total;
    else {
      index = Math.max(0, Math.min(index, state.total - 1));
      $('#view-counter').classList.remove('counter-flash');
      requestAnimationFrame(() => $('#view-counter').classList.add('counter-flash'));
    }
  }
  state.index = index;
  const offset = Math.max(0, index - 2);
  const result = await api(listURL({offset, limit: 5}));
  if (token !== navigation || state.view !== 'viewer') return;
  state.total = result.total;
  if (!state.total) { await closeViewer(); return; }
  if (index >= result.total) { await navigate(result.total - 1); return; }
  const item = result.items[index - offset];
  if (!item) { await navigate(Math.min(index, state.total - 1)); return; }
  state.current = item;
  state.index = index;
  original = false;
  image.classList.add('loading');
  image.alt = item.name;
  $('#view-counter').textContent = `${index + 1} / ${state.total.toLocaleString()}`;
  $('#view-name').textContent = item.name;
  $('#view-value').textContent = sortValue(item, index);
  writeHash();
  dispatch('viewer-item', item);
  for (const neighbour of result.items) if (neighbour.id !== item.id) previewURL(neighbour).catch(() => {});
  try {
    const url = await previewURL(item);
    if (token !== navigation) return;
    for (const oldURL of deferredRevoke) {
      if (oldURL !== url) { URL.revokeObjectURL(oldURL); deferredRevoke.delete(oldURL); }
    }
    retainedURL = url;
    image.onload = () => {
      if (token !== navigation) return;
      dimensions = {width: item.width || image.naturalWidth, height: item.height || image.naturalHeight};
      pan = {x: 0, y: 0};
      if (!state.prefs.lockRotation) { rotation = 0; flipX = 1; flipY = 1; }
      if (state.prefs.lockZoom) { mode = 'custom'; transform(); if (scale > fitScale()) loadOriginal(); }
      else applyMode('fit');
      image.classList.remove('loading');
      if (item.animated) loadOriginal();
    };
    image.onerror = () => toast('This image could not be displayed');
    image.src = url;
  } catch (error) {
    if (token === navigation && error.status === 404) {
      toast(error.message);
      await navigate(index);
      return;
    }
    if (token === navigation) { toast(error.message); image.removeAttribute('src'); image.classList.remove('loading'); }
  }
  if (state.prefs.info) renderInfo().catch(() => {});
}

export async function relocateCurrent() {
  if (state.view !== 'viewer') return;
  const id = state.current?.id;
  if (id) {
    try {
      const result = await api(`/api/roots/${state.root}/images/position?${query({image_id: id})}`, {quiet: true});
      state.index = result.index;
    } catch (error) { if (error.status !== 404) toast(error.message); }
  }
  await navigate(state.index);
}

function fitScale() {
  const bounds = displayBounds();
  const value = Math.min(stage.clientWidth / bounds.width, stage.clientHeight / bounds.height);
  return state.prefs.upscale ? value : Math.min(1, value);
}

export function applyMode(value) {
  mode = value;
  const bounds = displayBounds();
  scale = value === 'actual' ? 1 : value === 'fill' ? Math.max(stage.clientWidth / bounds.width, stage.clientHeight / bounds.height) : fitScale();
  pan = {x: 0, y: 0};
  transform();
  if (value === 'actual') loadOriginal();
}

export function zoomBy(factor, x = stage.clientWidth / 2, y = stage.clientHeight / 2) {
  const previous = scale;
  scale = Math.min(32, Math.max(.01, scale * factor));
  const origin = {x: x - stage.clientWidth / 2, y: y - stage.clientHeight / 2};
  pan.x = origin.x - (origin.x - pan.x) * scale / previous;
  pan.y = origin.y - (origin.y - pan.y) * scale / previous;
  mode = 'custom';
  transform();
  if (scale > 1 || Math.max(dimensions.width, dimensions.height) > 2048) loadOriginal();
}

export function panBy(x, y) { pan.x += x; pan.y += y; transform(); }

function displayBounds() {
  return Math.abs(rotation % 180) === 90 ? {width: dimensions.height, height: dimensions.width} : dimensions;
}

export function rotateView(degrees) {
  rotation = (rotation + degrees + 360) % 360;
  if (mode === 'fit' || mode === 'fill') applyMode(mode);
  else transform();
}

export function flipView(vertical = false) {
  if (vertical) flipY *= -1;
  else flipX *= -1;
  transform();
}

function transform() {
  const bounds = displayBounds();
  const overflowX = Math.max(0, (bounds.width * scale - stage.clientWidth) / 2);
  const overflowY = Math.max(0, (bounds.height * scale - stage.clientHeight) / 2);
  pan.x = Math.max(-overflowX, Math.min(overflowX, pan.x));
  pan.y = Math.max(-overflowY, Math.min(overflowY, pan.y));
  image.style.width = `${dimensions.width}px`;
  image.style.height = `${dimensions.height}px`;
  image.style.transform = `translate(calc(-50% + ${pan.x}px),calc(-50% + ${pan.y}px)) scale(${scale}) rotate(${rotation}deg) scale(${flipX},${flipY})`;
  $('#view-zoom').textContent = `${Math.round(scale * 100)}%`;
  stage.style.cursor = overflowX || overflowY ? 'grab' : '';
  stage.classList.toggle('zoomed', !!(overflowX || overflowY));
}

export function loadOriginal() {
  if (original || !state.current) return;
  original = true;
  const token = navigation;
  const source = `/api/images/${state.current.id}/file?v=${state.current.mtime}-${state.current.size}`;
  const loaded = new Image();
  loaded.onload = () => {
    if (token !== navigation) return;
    image.onload = null;
    image.src = source;
    transform();
  };
  loaded.onerror = () => { original = false; if (token === navigation) toast('The original could not be loaded'); };
  loaded.src = source;
}

export async function renderInfo() {
  $('#info-panel').hidden = !state.prefs.info;
  if (!state.prefs.info || !state.current) return;
  const id = state.current.id;
  const item = await api(`/api/images/${id}`);
  if (state.current?.id !== id) return;
  const panel = $('#info-panel');
  panel.replaceChildren(el('h2', {}, 'Image information'));
  const stars = el('div', {class: 'stars', 'aria-label': 'Rating'});
  for (let value = 1; value <= 5; value++) {
    const button = el('button', {'aria-label': `Rate ${value} stars`}, value <= item.rating ? '★' : '☆');
    button.onclick = () => dispatch('annotate', {rating: value});
    stars.append(button);
  }
  const clear = el('button', {title: 'Clear rating'}, 'Clear');
  clear.onclick = () => dispatch('annotate', {rating: 0});
  stars.append(clear);
  panel.append(stars);
  const favorite = el('button', {'aria-pressed': !!item.favorite}, item.favorite ? '♥ Favourite' : '♡ Favourite');
  favorite.onclick = () => dispatch('annotate', {favorite: !item.favorite});
  panel.append(favorite);
  const list = el('dl');
  for (const [label, value] of [
    ['Path', item.display_path], ['Size', sizeText(item.size)], ['Modified', dateText(item.mtime)],
    ['Created', dateText(item.ctime)], ['Taken', dateText(item.taken)],
    ['Dimensions', item.width ? `${item.width}×${item.height} (${(item.width * item.height / 1e6).toFixed(1)} MP)` : 'Unknown'],
    ['Orientation', item.orient], ['Format', `${item.format || item.ext}${item.animated ? ` · ${item.frames} frames` : ''}`],
    ['Camera', item.camera], ['Lens', item.lens], ['ISO', item.iso], ['Aperture', item.fnumber ? `f/${item.fnumber}` : null],
    ['Exposure', item.exposure], ['Focal length', item.focal ? `${item.focal} mm` : null], ['Decode error', item.meta_error],
  ]) {
    if (value !== null && value !== undefined) list.append(el('dt', {}, label), el('dd', {}, String(value)));
  }
  panel.append(list);
  if (item.gps_lat !== null && item.gps_lat !== undefined) {
    const coordinates = `${item.gps_lat}, ${item.gps_lon}`;
    list.append(el('dt', {}, 'GPS'), el('dd', {}, coordinates));
    const copy = el('button', {}, 'Copy coordinates');
    copy.onclick = () => navigator.clipboard.writeText(coordinates).catch(error => toast(error.message));
    panel.append(copy);
  }
  const sections = [
    ['Camera and exposure', /make|model|lens|iso|fnumber|exposure|focal|flash|meter|whitebalance/i],
    ['Dates and dimensions', /date|time|width|height|dimension|orientation|resolution/i],
    ['GPS', /^GPS/],
    ['Other EXIF', /./],
  ];
  const remaining = new Map(Object.entries(item.exif || {}));
  for (const [title, pattern] of sections) {
    const entries = [...remaining].filter(([tag]) => pattern.test(tag));
    if (!entries.length) continue;
    const details = el('details', {class: 'exif-section'});
    details.append(el('summary', {}, `${title} (${entries.length})`));
    const table = el('table');
    for (const [tag, value] of entries) {
      const row = el('tr');
      row.append(el('td', {}, tag), el('td', {}, String(value ?? '—')));
      table.append(row);
      remaining.delete(tag);
    }
    details.append(table);
    panel.append(details);
  }
}

export function initViewer() {
  $('#view-close').onclick = () => closeViewer().catch(() => {});
  $('.previous').onclick = () => navigate(-1, true).catch(() => {});
  $('.next').onclick = () => navigate(1, true).catch(() => {});
  $('[data-view="fit"]').onclick = () => applyMode('fit');
  $('[data-view="actual"]').onclick = () => applyMode('actual');
  $('[data-view="zoom-in"]').onclick = () => zoomBy(1.25);
  $('[data-view="zoom-out"]').onclick = () => zoomBy(.8);
  $('#view-info').onclick = () => { savePrefs({info: !state.prefs.info}); renderInfo().catch(() => {}); };
  viewer.addEventListener('pointermove', showOverlays, {passive: true});
  viewer.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    showOverlays();
    const buttons = [...viewer.querySelectorAll('button,select,summary')].filter(node => node.getClientRects().length && !node.disabled);
    if (event.shiftKey && (document.activeElement === buttons[0] || document.activeElement === viewer)) {
      event.preventDefault(); buttons.at(-1)?.focus();
    } else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {
      event.preventDefault(); buttons[0]?.focus();
    }
  });
  stage.addEventListener('wheel', event => {
    event.preventDefault();
    if (event.ctrlKey || state.prefs.wheelMode === 'zoom') zoomBy(Math.exp(-event.deltaY * .002), event.clientX, event.clientY);
    else if (!stage.wheelBusy) {
      navigate(event.deltaY > 0 ? 1 : -1, true).catch(() => {});
      stage.wheelBusy = true;
      setTimeout(() => { stage.wheelBusy = false; }, 180);
    }
  }, {passive: false});
  stage.addEventListener('dblclick', event => {
    if (event.target.closest('button')) return;
    if (mode === 'actual') applyMode('fit');
    else { zoomBy(1 / scale, event.clientX, event.clientY); mode = 'actual'; loadOriginal(); }
  });
  stage.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return;
    dragging = {x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y};
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    if (event.pointerType === 'touch' && pointers.size === 1) touchStart = {x: event.clientX, y: event.clientY, zoomed: stage.classList.contains('zoomed')};
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = {distance: Math.hypot(a.x - b.x, a.y - b.y)};
      touchStart = null;
      dragging = null;
    }
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener('pointermove', event => {
    if (pointers.has(event.pointerId)) pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const distance = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      zoomBy(distance / Math.max(1, pinch.distance), (a.x + b.x) / 2, (a.y + b.y) / 2);
      pinch.distance = distance;
      return;
    }
    if (!dragging) return;
    pan.x = dragging.panX + event.clientX - dragging.x;
    pan.y = dragging.panY + event.clientY - dragging.y;
    transform();
  });
  stage.addEventListener('pointerup', event => {
    if (touchStart && !touchStart.zoomed && pointers.size === 1) {
      const dx = event.clientX - touchStart.x;
      const dy = event.clientY - touchStart.y;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) navigate(dx < 0 ? 1 : -1, true).catch(() => {});
    }
    pointers.delete(event.pointerId);
    pinch = null; touchStart = null; dragging = null;
  });
  stage.addEventListener('pointercancel', () => { pointers.clear(); pinch = null; touchStart = null; dragging = null; });
  new ResizeObserver(() => { if (state.view === 'viewer') ['fit', 'fill'].includes(mode) ? applyMode(mode) : transform(); }).observe(stage);
  new ResizeObserver(() => {
    viewer.style.setProperty('--viewer-toolbar-height', `${$('#view-top').offsetHeight}px`);
  }).observe($('#view-top'));
  on('open-viewer', ({index}) => openViewer(index).catch(() => {}));
  on('prefs-changed', () => { $('#info-panel').hidden = !state.prefs.info; });
}
