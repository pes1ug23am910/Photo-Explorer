/** Shortcut definitions are also the source for the help dialog. */
import {$, el, dispatch, toast} from './api.js';
import {state, savePrefs} from './state.js';
import {selectIndex, selectAll, scrollToIndex, getColumns, getRowHeight} from './grid.js';
import {navigate, closeViewer, applyMode, zoomBy, panBy, loadOriginal, renderInfo, showOverlays, rotateView, flipView} from './viewer.js';
import {action, annotate, dialog, textDialog} from './panels.js';
import {slideshow, toggleSlideshow, stopSlideshow, copyImage, copyPath} from './playback.js';
import {comparing, openCompare, closeCompare, compareZoom, resetCompare} from './compare.js';
import {undoLast} from './operations_ui.js';

export const shortcuts = [
  ['Ctrl+Z', 'Undo the latest completed file operation after confirmation', 'Grid / viewer'],
  ['Enter / double-click', 'Open the focused image', 'Grid'],
  ['Arrows / Home / End / Page Up / Page Down', 'Move grid selection', 'Grid'],
  ['Ctrl+click / Shift+click / Ctrl+A', 'Toggle, range, or select all', 'Grid'],
  ['→ / Space / Page Down', 'Next image in the chosen order', 'Viewer'],
  ['← / Backspace / Page Up', 'Previous image', 'Viewer'],
  ['Home / End / Ctrl+G', 'First, last, or go to image number', 'Viewer'],
  ['Esc', 'Close dialog, menu, or viewer and return to current image', 'Everywhere'],
  ['F / 0', 'Fit without upscaling (configurable)', 'Viewer'],
  ['1', 'Actual size (1:1)', 'Viewer'],
  ['+ / − / Ctrl+wheel', 'Zoom; wheel zooms at the cursor', 'Viewer'],
  ['Double-click / drag / Shift+arrows', 'Fit ↔ 1:1, pan, or keyboard pan', 'Viewer'],
  ['O', 'Load original or full-size render', 'Viewer'],
  ['I / H', 'Toggle information / pin overlays', 'Viewer'],
  ['Shift+F / L', 'Fill viewport / lock zoom across images', 'Viewer'],
  ['R / Shift+R / V / Shift+V', 'Rotate clockwise / anticlockwise / flip horizontally / vertically', 'Viewer'],
  ['T / S', 'Toggle filmstrip / start or pause slideshow', 'Viewer'],
  ['Space / Esc (slideshow)', 'Pause or resume / stop slideshow', 'Viewer'],
  ['Ctrl+C / Ctrl+Shift+C', 'Copy image as PNG / copy file path', 'Viewer'],
  ['B', 'Toggle sidebar', 'Everywhere'],
  ['Delete', 'Move to Recycle Bin', 'Grid / viewer'],
  ['M / C / F2', 'Move / copy / rename', 'Grid / viewer'],
  ['Ctrl+E / Ctrl+O', 'Reveal in Explorer / open with default app', 'Grid / viewer'],
  ['2–5 / Alt+1', 'Rate 2–5 stars / 1 star', 'Grid / viewer'],
  ['` / Ctrl+0', 'Clear rating', 'Grid / viewer'],
  ['Ctrl+D', 'Toggle favourite', 'Grid / viewer'],
  ['X', 'Compare 2–4 selected images with synchronized zoom/pan', 'Grid'],
  ['Ctrl+R / W', 'Confirm rotation on disk / set wallpaper', 'Grid / viewer'],
  ['?', 'Show this shortcut reference', 'Everywhere'],
];

export function showHelp() {
  const table = el('table', {class: 'shortcut-table'});
  for (const [key, description, context] of shortcuts) {
    const row = el('tr');
    const first = el('td');
    first.append(el('kbd', {}, key));
    row.append(first, el('td', {}, description), el('td', {class: 'muted'}, context));
    table.append(row);
  }
  dialog('Keyboard shortcuts', table);
}

export function initKeys() {
  $('#help').onclick = showHelp;
  document.addEventListener('keydown', async event => {
    if (event.target.closest('input,textarea,select,[contenteditable=true]') || $('#modal').open) return;
    if (event.target.closest('button,summary,a') && ['Enter', ' '].includes(event.key)) return;
    const key = event.key.toLowerCase();
    const control = event.ctrlKey || event.metaKey;
    const viewing = state.view === 'viewer';
    let handler = null;
    if (!$('#context-menu').hidden && ['arrowdown', 'arrowup', 'home', 'end'].includes(key)) {
      const buttons = [...$('#context-menu').querySelectorAll('button')];
      const current = buttons.indexOf(document.activeElement);
      const next = key === 'home' ? 0 : key === 'end' ? buttons.length - 1 :
        (current + (key === 'arrowdown' ? 1 : -1) + buttons.length) % buttons.length;
      event.preventDefault();
      buttons[next]?.focus();
      return;
    }
    if (comparing()) {
      if (key === 'escape') handler = closeCompare;
      else if (['+', '='].includes(key)) handler = () => compareZoom(1.25);
      else if (key === '-') handler = () => compareZoom(.8);
      else if (key === 'f' || key === '0') handler = resetCompare;
      if (handler) { event.preventDefault(); handler(); }
      return;
    }
    if (key === 'escape') handler = () => {
      if (!$('#context-menu').hidden) { $('#context-menu').hidden = true; viewing ? $('#viewer').focus() : $('#grid').focus(); }
      else if (viewing && slideshow.active) stopSlideshow();
      else if (viewing) return closeViewer();
      else { state.selected.clear(); dispatch('selection-changed'); }
    };
    else if (key === '?') handler = showHelp;
    else if (control && key === 'z') handler = undoLast;
    else if (control && key === 'a' && !viewing) handler = selectAll;
    else if (control && key === 'e') handler = () => action('reveal');
    else if (control && key === 'o') handler = () => action('open');
    else if (control && key === 'd') handler = () => action('favorite');
    else if (control && key === 'c' && viewing) handler = event.shiftKey ? copyPath : copyImage;
    else if (control && key === 'r') handler = () => action('rotate');
    else if (control && key === '0' || key === '`') handler = () => annotate({rating: 0});
    else if (control && key === 'g' && viewing) handler = async () => {
      const result = await textDialog('Go to image', String(state.index + 1), `Number (1–${state.total})`, 'number');
      if (result !== null && Number.isInteger(Number(result))) await navigate(Number(result) - 1);
    };
    else if (!control && key === 'b') handler = () => savePrefs({sidebar: !state.prefs.sidebar});
    else if (!control && key === 'delete') handler = () => action('trash');
    else if (!control && key === 'm') handler = () => action('move');
    else if (!control && key === 'c') handler = () => action('copy');
    else if (!control && key === 'w') handler = () => action('wallpaper');
    else if (!control && key === 'x' && !viewing) handler = openCompare;
    else if (key === 'f2') handler = () => action('rename');
    else if (!control && /^[2-5]$/.test(key) || event.altKey && key === '1') handler = () => annotate({rating: Number(key)});
    else if (viewing) {
      if (event.shiftKey && key.startsWith('arrow')) handler = () => panBy(
        key === 'arrowleft' ? 60 : key === 'arrowright' ? -60 : 0, key === 'arrowup' ? 60 : key === 'arrowdown' ? -60 : 0);
      else if (key === ' ' && slideshow.active) handler = toggleSlideshow;
      else if (['arrowright', ' ', 'pagedown'].includes(key)) handler = () => navigate(1, true);
      else if (['arrowleft', 'backspace', 'pageup'].includes(key)) handler = () => navigate(-1, true);
      else if (key === 'home') handler = () => navigate(0);
      else if (key === 'end') handler = () => navigate(state.total - 1);
      else if (key === 'f' || key === '0') handler = () => applyMode(event.shiftKey && key === 'f' ? 'fill' : 'fit');
      else if (key === '1') handler = () => applyMode('actual');
      else if (key === '+' || key === '=') handler = () => zoomBy(1.25);
      else if (key === '-') handler = () => zoomBy(.8);
      else if (key === 'o') handler = loadOriginal;
      else if (key === 'i') handler = () => { savePrefs({info: !state.prefs.info}); return renderInfo(); };
      else if (key === 'h') handler = () => { savePrefs({pinOverlays: !state.prefs.pinOverlays}); showOverlays(); };
      else if (key === 'l') handler = () => savePrefs({lockZoom: !state.prefs.lockZoom});
      else if (key === 'r') handler = () => rotateView(event.shiftKey ? -90 : 90);
      else if (key === 'v') handler = () => flipView(event.shiftKey);
      else if (key === 't') handler = () => savePrefs({filmstrip: !state.prefs.filmstrip});
      else if (key === 's') handler = toggleSlideshow;
    } else {
      if (key === 'enter') handler = () => dispatch('open-viewer', {index: state.focusIndex});
      const columns = getColumns();
      const page = columns * Math.max(1, Math.floor($('#grid').clientHeight / getRowHeight()));
      const index = {arrowright: state.focusIndex + 1, arrowleft: state.focusIndex - 1,
        arrowdown: state.focusIndex + columns, arrowup: state.focusIndex - columns,
        home: 0, end: state.total - 1, pagedown: state.focusIndex + page, pageup: state.focusIndex - page}[key];
      if (index !== undefined) handler = async () => { await selectIndex(index, event); await scrollToIndex(index); };
    }
    if (handler) {
      event.preventDefault();
      try { await handler(); } catch (error) { if (!error.status) toast(error.message); }
    }
  });
}
