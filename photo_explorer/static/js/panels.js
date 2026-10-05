/** Accessible dialogs and explicit, recoverable local file actions. */
import {$, $$, el, api, post, dispatch, on, toast} from './api.js';
import {state, savePrefs} from './state.js';
import {refreshGrid, itemAt} from './grid.js';
import {navigate, renderInfo, relocateCurrent} from './viewer.js';

let pendingDialog = null;
const modal = $('#modal');

export function dialog(title, content, actions = [{label: 'Close', value: 'cancel'}]) {
  if (modal.open) return Promise.resolve('cancel');
  dispatch('dialog-open');
  const previousFocus = document.activeElement;
  $('#modal-title').textContent = title;
  $('#modal-body').replaceChildren(content);
  $('#modal-footer').replaceChildren(...actions.map(action => el('button', {
    value: action.value, class: action.primary ? 'primary' : '', type: 'submit',
  }, action.label)));
  return new Promise(resolve => {
    pendingDialog = {resolve, previousFocus};
    modal.returnValue = 'cancel';
    modal.showModal();
    const focus = $('input,select,button', $('#modal-body')) || $('button', $('#modal-footer'));
    focus?.focus();
  });
}

export async function confirmAction(title, message) {
  const content = el('p', {}, message);
  return await dialog(title, content, [{label: 'Cancel', value: 'cancel'}, {label: 'Confirm', value: 'ok', primary: true}]) === 'ok';
}

export async function textDialog(title, value = '', label = 'Name', type = 'text') {
  const content = el('div');
  const input = el('input', {type, value, 'aria-label': label});
  const caption = el('label', {}, label);
  caption.append(input);
  caption.style.display = 'block';
  content.append(caption);
  const result = await dialog(title, content, [{label: 'Cancel', value: 'cancel'}, {label: 'Apply', value: 'ok', primary: true}]);
  return result === 'ok' ? input.value : null;
}

export async function browseFolder(initial = '', title = 'Choose a folder', showRecursive = false) {
  const content = el('div');
  const input = el('input', {type: 'text', value: initial, placeholder: 'Absolute folder path', 'aria-label': 'Folder path'});
  const controls = el('div', {class: 'ordering'});
  const drives = el('button', {type: 'button'}, 'Drives');
  const up = el('button', {type: 'button'}, 'Up');
  const load = el('button', {type: 'button'}, 'Browse path');
  controls.append(drives, up, load);
  const list = el('div', {class: 'folders'});
  const recursive = el('input', {type: 'checkbox', id: 'recursive-choice'});
  recursive.checked = true;
  const label = el('label', {}, 'Include subfolders');
  label.prepend(recursive);
  content.append(input, controls, list);
  if (showRecursive) content.append(label);
  if (/^(Move|Copy)/.test(title)) {
    try {
      const destinations = await api('/api/destinations');
      if (destinations.length) {
        const recent = el('select', {'aria-label': 'Recent destination'});
        recent.append(el('option', {value: ''}, 'Recent destinations…'));
        for (const destination of destinations) recent.append(el('option', {value: destination.path}, destination.path));
        recent.onchange = () => { if (recent.value) populate(recent.value); };
        content.prepend(recent);
      }
    } catch { /* Folder browsing remains usable if recent history fails. */ }
  }
  let browseToken = 0;
  async function populate(path) {
    const token = ++browseToken;
    try {
      const folders = await api(`/api/fs/browse?${new URLSearchParams(path ? {path} : {})}`);
      if (token !== browseToken) return;
      input.value = path;
      list.replaceChildren(...folders.map(folder => {
        const button = el('button', {type: 'button'}, `▸ ${folder.name}`);
        button.onclick = () => populate(folder.path);
        return button;
      }));
      if (!folders.length) list.append(el('p', {class: 'muted'}, 'No subfolders. Choose this folder or go up.'));
    } catch { /* Request helper reports errors without closing the dialog. */ }
  }
  drives.onclick = () => populate('');
  load.onclick = () => populate(input.value);
  input.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); populate(input.value); } };
  up.onclick = () => {
    let path = input.value.replace(/[\\/]+$/, '').replace(/[\\/][^\\/]+$/, '');
    if (/^[A-Za-z]:$/.test(path)) path += '\\';
    if (path === input.value) path = '';
    populate(path);
  };
  populate(initial);
  const result = await dialog(title, content, [{label: 'Cancel', value: 'cancel'}, {label: 'Choose folder', value: 'ok', primary: true}]);
  browseToken++;
  return result === 'ok' && input.value ? {path: input.value, recursive: recursive.checked} : null;
}

export async function chooseFolder(fallback = false) {
  if (!fallback) {
    try {
      const result = await post('/api/fs/pick');
      if (result.path) return {path: result.path, recursive: true};
      return null;
    } catch { /* The in-app browser remains available if Tk is unavailable. */ }
  }
  return browseFolder('', 'Open a photo folder', true);
}

async function destinationDialog(kind, count) {
  return browseFolder('', `${kind === 'move' ? 'Move' : 'Copy'} ${count} image${count === 1 ? '' : 's'} to…`);
}

async function actionIds() {
  if (state.view === 'viewer' && state.current) return [state.current.id];
  if (state.selected.size) return [...state.selected];
  const item = await itemAt(state.focusIndex);
  return item ? [item.id] : [];
}

export async function annotate(fields) {
  const ids = await actionIds();
  for (const id of ids) await api(`/api/images/${id}`, {method: 'PATCH', body: fields});
  await refreshGrid();
  if (state.view === 'viewer') { await relocateCurrent(); await renderInfo(); }
  dispatch('library-changed');
}

export async function action(kind) {
  $('#context-menu').hidden = true;
  const ids = await actionIds();
  if (!ids.length) return;
  let body = {};
  if (kind === 'trash' && !state.prefs.skipConfirm && !await confirmAction('Move to Recycle Bin?',
    `${ids.length} image${ids.length === 1 ? '' : 's'} will be moved to the Windows Recycle Bin. You can restore them there.`)) return;
  if (kind === 'move' || kind === 'copy') {
    const result = await destinationDialog(kind, ids.length);
    if (!result) return;
    body = {dest: result.path};
  }
  if (kind === 'rename') {
    if (ids.length !== 1) { toast('Select one image to rename'); return; }
    const item = await api(`/api/images/${ids[0]}`);
    const name = await textDialog('Rename image', item.name, 'New filename');
    if (name === null || name === item.name) return;
    body = {name};
  }
  if (kind === 'rotate') {
    if (!await confirmAction('Rotate files on disk?', `Rotate ${ids.length} image${ids.length === 1 ? '' : 's'} clockwise by 90°? This re-encodes and replaces the original pixels. JPEGs are saved at quality 95. This cannot be undone here.`)) return;
    body = {degrees: 90};
  }
  if (kind === 'wallpaper') {
    if (ids.length !== 1) { toast('Select one image for wallpaper'); return; }
    if (!await confirmAction('Set desktop wallpaper?', 'Replace the current Windows wallpaper with this image?')) return;
    await post(`/api/images/${ids[0]}/wallpaper`);
    toast('Wallpaper updated', true);
    return;
  }
  if (kind === 'rate') {
    const rating = await textDialog('Rate selected images', '0', 'Rating from 0 to 5', 'number');
    if (rating === null) return;
    const value = Number(rating);
    if (!Number.isInteger(value) || value < 0 || value > 5) { toast('Rating must be 0–5'); return; }
    await annotate({rating: value});
    return;
  }
  if (kind === 'favorite') {
    const item = await api(`/api/images/${ids[0]}`);
    await annotate({favorite: !item.favorite});
    return;
  }
  if (['reveal', 'open'].includes(kind)) {
    await post(`/api/images/${ids[0]}/${kind}`);
    return;
  }
  let done = 0;
  try {
    for (const id of ids) { await post(`/api/images/${id}/${kind}`, body); done++; }
  } finally {
    state.selected.clear();
    await refreshGrid();
    if (state.view === 'viewer') {
      if (kind === 'trash' || kind === 'move') await navigate(Math.min(state.index, state.total - 1));
      else await relocateCurrent();
    }
    dispatch('library-changed');
    if (done) toast(`${done} image${done === 1 ? '' : 's'}: ${kind} complete`, true);
  }
}

export function contextMenu({x, y}) {
  const menu = $('#context-menu');
  menu.replaceChildren();
  for (const [kind, label] of [['move', 'Move…'], ['copy', 'Copy…'], ['rename', 'Rename…'],
    ['trash', 'Move to Recycle Bin…'], ['rate', 'Rate…'], ['favorite', 'Toggle favourite'],
    ['reveal', 'Reveal in Explorer'], ['open', 'Open with default app'],
    ['rotate', 'Rotate file 90°…'], ['wallpaper', 'Set as wallpaper…']]) {
    const button = el('button', {role: 'menuitem'}, label);
    button.onclick = () => action(kind).catch(() => {});
    menu.append(button);
  }
  menu.hidden = false;
  menu.style.left = `${Math.min(x, innerWidth - 215)}px`;
  menu.style.top = `${Math.min(y, innerHeight - menu.offsetHeight - 12)}px`;
  $('button', menu)?.focus();
}

export async function settingsDialog() {
  const content = el('div', {class: 'settings-form'});
  const controls = {};
  for (const [key, label] of [['upscale', 'Upscale small images in Fit mode'], ['skipConfirm', 'Skip Recycle Bin confirmation'],
    ['wrap', 'Wrap navigation at the first and last image'], ['lockRotation', 'Keep view rotation and flips across images'],
    ['slideshowLoop', 'Loop slideshow at the end'], ['fade', 'Fade between slideshow images']]) {
    const line = el('label', {});
    const input = el('input', {type: 'checkbox'});
    input.checked = !!state.prefs[key];
    line.append(input, document.createTextNode(label));
    line.style.display = 'flex';
    line.style.margin = '12px 0';
    controls[key] = input;
    content.append(line);
  }
  const wheel = el('select', {'aria-label': 'Wheel mode'});
  wheel.append(el('option', {value: 'navigate'}, 'Wheel navigates'), el('option', {value: 'zoom'}, 'Wheel zooms'));
  wheel.value = state.prefs.wheelMode;
  content.append(wheel);
  const interval = el('input', {type: 'number', min: 1, max: 60, value: state.prefs.slideInterval || 4, 'aria-label': 'Slideshow interval in seconds'});
  const intervalLabel = el('label', {}, 'Slideshow interval (1–60 seconds)');
  intervalLabel.append(interval);
  content.append(intervalLabel);
  const result = await dialog('Viewer settings', content, [{label: 'Cancel', value: 'cancel'}, {label: 'Save', value: 'ok', primary: true}]);
  if (result === 'ok') savePrefs({...Object.fromEntries(Object.entries(controls).map(([key, input]) => [key, input.checked])),
    wheelMode: wheel.value, slideInterval: Math.max(1, Math.min(60, Number(interval.value) || 4))});
}

export function initPanels() {
  modal.addEventListener('close', () => {
    const pending = pendingDialog;
    pendingDialog = null;
    pending?.resolve(modal.returnValue);
    pending?.previousFocus?.focus({preventScroll: true});
  });
  modal.addEventListener('cancel', () => { modal.returnValue = 'cancel'; });
  modal.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const focusable = $$('button,input,select,textarea,a[href]', modal).filter(node => !node.disabled && !node.hidden);
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1)?.focus(); }
    else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0]?.focus(); }
  });
  $$('[data-batch]').forEach(button => { button.onclick = () => action(button.dataset.batch).catch(() => {}); });
  on('annotate', fields => annotate(fields).catch(() => {}));
  on('context-menu', contextMenu);
  $('#view-menu').onclick = event => contextMenu({x: event.clientX - 150, y: event.clientY + 20});
  document.addEventListener('pointerdown', event => { if (!event.target.closest('#context-menu,#view-menu')) $('#context-menu').hidden = true; });
  const settings = el('button', {id: 'settings', title: 'Viewer settings'}, 'Settings');
  settings.onclick = () => settingsDialog().catch(() => {});
  $('#topbar').insertBefore(settings, $('#help'));
}
