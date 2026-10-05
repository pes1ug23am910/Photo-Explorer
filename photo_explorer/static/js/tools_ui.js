/** Tools menu, folder navigation, flatten preview, and structure export. */
import {$, el, post, on, debounce, toast, sizeText, dispatch} from './api.js';
import {state, changeQuery, savePrefs} from './state.js';
import {dialog} from './panels.js';
import {input, select, line, button, destination, planTable} from './tool_forms.js';
import {initOperations, showOperations, watchOperation} from './operations_ui.js';
import {rulesDialog} from './rules_ui.js';
import {structureOptions} from './operation_logic.js';
import {duplicatesDialog} from './duplicates_ui.js';

const imageExtensions = 'jpg,jpeg,jpe,jfif,png,gif,webp,bmp,tif,tiff,heic,heif,avif,ico,psd,svg';
export const currentFolder = () => state.filters.dir || state.rootRecord?.path || '';

export function openFolder(path, mode = state.prefs.dirMode || 'subtree') {
  savePrefs({dirMode: mode});
  changeQuery({filters: {...state.filters, dir: path, dir_mode: mode}});
}

export function breadcrumbs() {
  const target = $('#breadcrumb');
  const root = state.rootRecord?.path;
  const path = currentFolder();
  target.replaceChildren();
  $('#folder-up').disabled = !root || path.toLowerCase() === root.toLowerCase();
  if (!root) { target.textContent = 'Your images, in your order.'; return; }
  let current = root.replace(/[\\/]+$/, '');
  target.append(button(root, () => openFolder(root)));
  for (const part of path.slice(root.length).split(/[\\/]+/).filter(Boolean)) {
    current += '\\' + part;
    const destination = current;
    target.append(el('span', {}, ' / '), button(part, () => openFolder(destination)));
  }
}

export async function flattenDialog(path = currentFolder()) {
  const defaults = state.prefs.flattenDefaults || {};
  const content = el('div', {class: 'tool-form'});
  const target = destination('Target', path);
  const dest = destination('Destination', path);
  const mode = select('Action', ['move', 'copy'], defaults.mode);
  const controls = {
    prefix: input('Prefix with folder chain', defaults.prefix, 'checkbox'),
    sep: input('Separator', defaults.sep ?? '_'), strip_tags: input('Strip bracket tags', defaults.strip_tags, 'checkbox'),
    max_prefix: input('Maximum prefix (0 = unlimited)', defaults.max_prefix || 0, 'number'),
    ext: input('Extensions', defaults.ext || ''), exclude: input('Exclude filename globs', defaults.exclude || ''),
    skip_duplicates: input('Skip identical collisions', defaults.skip_duplicates ?? true, 'checkbox'),
    remove_empty_dirs: input('Remove empty folders after move', defaults.remove_empty_dirs ?? true, 'checkbox'),
  };
  controls.max_prefix.min = 0;
  const imagesOnly = input('Images only', false, 'checkbox');
  const preview = el('div', {class: 'tool-preview'});
  const progress = el('p', {role: 'status'});
  let expected = null;
  let encoded = '';
  function read() {
    return {target: target.control.value, dest: dest.control.value, mode: mode.value,
      ...Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.type === 'checkbox' ? control.checked : control.type === 'number' ? Number(control.value) : control.value]))};
  }
  const previewButton = button('Preview', async () => {
    const body = read();
    const result = await post('/api/flatten/plan', body);
    expected = result.total; encoded = JSON.stringify(body);
    const s = result.summary;
    preview.replaceChildren(el('p', {}, `${s.take} files · ${sizeText(s.bytes)} · ${s.skip} skipped · ${s.filtered} filtered · ${s.conflicts_renamed} renamed${result.total > 2000 ? ' · first 2000 shown' : ''}`),
      planTable(result.entries.map(entry => ({...entry, src: entry.src.slice(body.target.replace(/[\\/]+$/, '').length + 1)}))));
    run.disabled = !s.take;
    savePrefs({flattenDefaults: body});
  });
  const run = button('Run', async () => {
    const body = read();
    if (expected === null || encoded !== JSON.stringify(body)) { toast('Preview these options first'); return; }
    const result = await post('/api/flatten/run', {...body, expected_total: expected});
    expected = null;
    await watchOperation(result.operation_id, progress);
  });
  run.disabled = true;
  const buttons = el('div', {class: 'tool-actions'});
  buttons.append(previewButton, run);
  content.append(target.row, dest.row, line('Action', mode));
  for (const control of Object.values(controls)) content.append(line(control.getAttribute('aria-label'), control));
  content.append(line('Images only', imagesOnly), buttons, progress, preview);
  imagesOnly.onchange = () => { controls.ext.value = imagesOnly.checked ? imageExtensions : ''; };
  const invalidate = () => { expected = null; run.disabled = true; };
  content.addEventListener('input', invalidate); content.addEventListener('change', invalidate);
  await dialog('Flatten folders', content);
}

export async function structureDialog(path = currentFolder()) {
  const content = el('div', {class: 'tool-form'});
  const target = destination('Folder', path);
  const files = input('Include files', true, 'checkbox');
  const skip = input('Skip folder names or absolute paths (comma-separated)', '');
  const dest = input('Save under folder (blank = source folder)', '');
  const preview = el('pre', {class: 'structure-preview'});
  let text = '';
  let saved = null;
  let generation = 0;
  async function refresh() {
    const token = ++generation;
    const response = await fetch(`/api/fs/structure?${structureOptions(target.control.value, files.checked, skip.value)}`);
    if (!response.ok) { const result = await response.json(); throw new Error(result.detail); }
    const full = await response.text();
    if (token !== generation) return;
    text = full;
    preview.textContent = text.split('\n').slice(0, 400).join('\n');
  }
  const actions = el('div', {class: 'tool-actions'});
  actions.append(button('Preview', refresh), button('Copy to clipboard', async () => { await refresh(); await navigator.clipboard.writeText(text); toast('Structure copied', true); }),
    button('Save to file', async () => {
      saved = await post('/api/fs/structure/save', {path: target.control.value, files: files.checked, skip: skip.value, dest_dir: dest.value || null});
      toast(`Saved ${saved.path}`, true);
    }), button('Reveal', () => post('/api/fs/reveal', {path: saved?.path || target.control.value})));
  content.append(target.row, line('Include files', files), line('Skip', skip), line('Save under', dest), actions,
    el('small', {}, 'Preview shows the first 400 lines. Copy and Save include the entire structure.'), preview);
  content.addEventListener('input', debounce(() => refresh().catch(error => { preview.textContent = error.message; }), 300));
  if (path) refresh().catch(error => { preview.textContent = error.message; });
  await dialog('Export structure', content);
  generation++;
}

function menu(x, y, path = currentFolder(), folder = false) {
  const target = $('#context-menu');
  const choices = folder ? [
    ['Open as library', () => dispatch('open-library', path)],
    ['This folder only', () => openFolder(path, 'exact')], ['Include subfolders', () => openFolder(path, 'subtree')],
  ] : [['Operations', () => showOperations()]];
  choices.push(['Flatten…', () => flattenDialog(path)], ['Rules…', () => rulesDialog(path)],
    ['Export structure…', () => structureDialog(path)]);
  if (!folder) choices.push(['Duplicates…', duplicatesDialog]);
  if (folder) choices.push(['Reveal in Explorer', () => post('/api/fs/reveal', {path})]);
  target.replaceChildren(...choices.map(([label, handler]) => button(label, async () => { target.hidden = true; await handler(); })));
  target.hidden = false;
  target.style.left = `${Math.min(x, innerWidth - 215)}px`;
  target.style.top = `${Math.min(y, innerHeight - target.offsetHeight - 12)}px`;
  target.querySelector('button')?.focus();
}

export function initTools() {
  initOperations();
  const tools = button('Tools', () => {
    const bounds = tools.getBoundingClientRect(); menu(bounds.left, bounds.bottom);
  });
  tools.id = 'tools';
  $('#topbar').insertBefore(tools, $('#help'));
  const up = button('Up', () => {
    const path = currentFolder().replace(/[\\/]+$/, '').replace(/[\\/][^\\/]+$/, '');
    openFolder(path.length < state.rootRecord.path.length ? state.rootRecord.path : path);
  });
  up.id = 'folder-up';
  $('#locationbar').insertBefore(up, $('#breadcrumb'));
  const modes = select('Folder mode', [['subtree', 'Include subfolders'], ['exact', 'This folder only']], state.prefs.dirMode || 'subtree');
  modes.onchange = () => openFolder(currentFolder(), modes.value);
  const controls = el('div', {id: 'folder-controls'});
  controls.append(modes);
  $('#sidebar').insertBefore(controls, $('#folder-tree'));
  $('#selection-toolbar').insertBefore(button('Apply rule to selection…', () => rulesDialog(null, [...state.selected])), $('#clear-selection'));
  on('folder-open', path => openFolder(path));
  on('folder-context', ({x, y, path}) => menu(x, y, path, true));
  on('query-changed', () => { modes.value = state.filters.dir_mode || 'subtree'; breadcrumbs(); });
  on('root-changed', breadcrumbs);
  on('prefs-changed', () => { modes.value = state.prefs.dirMode || 'subtree'; });
  on('viewer-neighbourhood', ids => post('/api/cache/protect', {ids}).catch(() => {}));
  breadcrumbs();
}
