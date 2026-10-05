/** Two-to-four pane comparison with shared relative zoom and pan. */
import {$, el, api, on, toast} from './api.js';
import {state, query, sortValue} from './state.js';

let overlay;
let panes = [];
let zoom = 1;
let pan = {x: 0, y: 0};
let drag = null;
let openToken = 0;

function backgroundInert(value) {
  for (const id of ['topbar', 'locationbar', 'workspace', 'statusbar', 'selection-toolbar']) {
    $(`#${id}`).inert = value;
  }
}

export function comparing() { return overlay && !overlay.hidden; }

export function closeCompare() {
  openToken++;
  if (!overlay) return;
  overlay.hidden = true;
  if (state.view !== 'viewer') backgroundInert(false);
  panes = [];
  $('#compare-panes').replaceChildren();
  $('#grid').focus({preventScroll: true});
}

function transformPanes() {
  for (const pane of panes) {
    const width = pane.item.width || pane.image.naturalWidth || 1;
    const height = pane.item.height || pane.image.naturalHeight || 1;
    const fit = Math.min(1, pane.stage.clientWidth / width, pane.stage.clientHeight / height);
    pane.image.style.width = `${width}px`;
    pane.image.style.height = `${height}px`;
    pane.image.style.transform = `translate(calc(-50% + ${pan.x * pane.stage.clientWidth}px),calc(-50% + ${pan.y * pane.stage.clientHeight}px)) scale(${fit * zoom})`;
    if (zoom > 1 && !pane.original) {
      pane.original = true;
      pane.image.src = `/api/images/${pane.item.id}/file?v=${pane.item.mtime}-${pane.item.size}`;
    }
  }
  $('#compare-zoom').textContent = `${Math.round(zoom * 100)}% of fit`;
}

export function compareZoom(factor, x = .5, y = .5) {
  const previous = zoom;
  zoom = Math.max(.1, Math.min(20, zoom * factor));
  pan.x = (x - .5) - ((x - .5) - pan.x) * zoom / previous;
  pan.y = (y - .5) - ((y - .5) - pan.y) * zoom / previous;
  transformPanes();
}

export function resetCompare() { zoom = 1; pan = {x: 0, y: 0}; transformPanes(); }

export async function openCompare() {
  if (state.selected.size < 2 || state.selected.size > 4) { toast('Select 2–4 images to compare'); return; }
  const token = ++openToken;
  const ids = [...state.selected];
  const items = await Promise.all(ids.map(async id => {
    const [item, position] = await Promise.all([
      api(`/api/images/${id}`), api(`/api/roots/${state.root}/images/position?${query({image_id: id})}`),
    ]);
    return {item, index: position.index};
  }));
  if (token !== openToken) return;
  items.sort((a, b) => a.index - b.index);
  const container = $('#compare-panes');
  container.replaceChildren();
  container.dataset.count = items.length;
  panes = [];
  for (const {item, index} of items) {
    const pane = el('section', {class: 'compare-pane'});
    const header = el('header');
    header.append(el('strong', {}, item.name), el('small', {}, sortValue(item, index)));
    const stage = el('div', {class: 'compare-stage'});
    const image = el('img', {alt: item.name, draggable: false});
    const record = {item, stage, image, original: false};
    image.onload = transformPanes;
    image.onerror = () => toast(`Cannot display ${item.name}`);
    image.src = `/api/images/${item.id}/preview?v=${item.mtime}-${item.size}`;
    stage.append(image);
    pane.append(header, stage);
    panes.push(record);
    stage.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = stage.getBoundingClientRect();
      compareZoom(Math.exp(-event.deltaY * .002), (event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
    }, {passive: false});
    stage.addEventListener('pointerdown', event => {
      drag = {x: event.clientX, y: event.clientY, pan: {...pan}, width: stage.clientWidth, height: stage.clientHeight};
      stage.setPointerCapture(event.pointerId);
    });
    stage.addEventListener('pointermove', event => {
      if (!drag) return;
      pan.x = drag.pan.x + (event.clientX - drag.x) / drag.width;
      pan.y = drag.pan.y + (event.clientY - drag.y) / drag.height;
      transformPanes();
    });
    stage.addEventListener('pointerup', () => { drag = null; });
    stage.addEventListener('pointercancel', () => { drag = null; });
    stage.ondblclick = resetCompare;
    container.append(pane);
  }
  overlay.hidden = false;
  backgroundInert(true);
  overlay.focus();
  resetCompare();
}

export function initCompare() {
  overlay = el('section', {id: 'compare', tabindex: '-1', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Compare selected images'});
  overlay.hidden = true;
  const toolbar = el('header', {class: 'compare-toolbar'});
  const close = el('button', {title: 'Close comparison (Esc)'}, '✕');
  close.onclick = closeCompare;
  const fit = el('button', {}, 'Reset');
  fit.onclick = resetCompare;
  const minus = el('button', {'aria-label': 'Zoom out'}, '−');
  minus.onclick = () => compareZoom(.8);
  const plus = el('button', {'aria-label': 'Zoom in'}, '+');
  plus.onclick = () => compareZoom(1.25);
  toolbar.append(close, el('strong', {}, 'Compare'), el('span', {class: 'spacer'}),
    el('small', {}, 'Zoom and pan are synchronized'), minus, el('span', {id: 'compare-zoom'}), plus, fit);
  overlay.append(toolbar, el('div', {id: 'compare-panes'}));
  document.body.append(overlay);
  overlay.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const buttons = [...overlay.querySelectorAll('button')];
    if (event.shiftKey && (document.activeElement === buttons[0] || document.activeElement === overlay)) {
      event.preventDefault(); buttons.at(-1)?.focus();
    } else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {
      event.preventDefault(); buttons[0]?.focus();
    }
  });
  const button = el('button', {id: 'compare-selection', title: 'Compare 2–4 selected images (X)'}, 'Compare');
  button.onclick = () => openCompare().catch(() => {});
  $('#selection-toolbar').insertBefore(button, $('#clear-selection'));
  new ResizeObserver(() => { if (comparing()) transformPanes(); }).observe(overlay);
  on('root-changed', closeCompare);
}
