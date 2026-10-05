/** Pollable journal panel, named undo confirmation, and action feedback. */
import {$, el, api, post, dispatch, on, toast, dateText} from './api.js';
import {state, savePrefs} from './state.js';
import {dialog, confirmAction} from './panels.js';
import {refreshGrid} from './grid.js';
import {relocateCurrent} from './viewer.js';
import {newestUndoable} from './operation_logic.js';
import {button, planTable} from './tool_forms.js';

let active = false;
const watched = new Set();

export async function undoOperation(id) {
  if ($('#modal').open) {
    $('#modal').close('cancel');
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  const op = await api(`/api/operations/${id}`);
  if (!await confirmAction('Undo operation?', `Undo “${op.label}”? Existing files will never be overwritten.`)) return;
  const result = await post(`/api/operations/${id}/undo`);
  await watchOperation(result.operation_id);
}

export async function undoLast() {
  const op = newestUndoable(await api('/api/operations?undoable=true&limit=1'));
  if (!op) { toast('No completed operation can be undone'); return; }
  await undoOperation(op.id);
}

export async function watchOperation(id, output = null) {
  if (watched.has(id)) return;
  watched.add(id);
  try {
    let op;
    do {
      op = await api(`/api/operations/${id}`);
      if (output) output.textContent = `${op.label} · ${op.done + op.failed}/${op.total} · ${op.status}`;
      if (active) await refreshOperations();
      if (op.status === 'running') await new Promise(resolve => setTimeout(resolve, 700));
    } while (op.status === 'running');
    await refreshGrid();
    if (state.view === 'viewer') await relocateCurrent();
    dispatch('library-changed');
    toast(`${op.label}: ${op.done} completed, ${op.failed} failed · ${op.status}`, !op.failed,
      op.undoable && !op.undone_by && op.status === 'done' ? op.id : null);
  } finally { watched.delete(id); }
}

async function showEntries(op) {
  const content = el('div');
  let offset = 0;
  const rows = el('div');
  const more = button('Show more entries', load);
  async function load() {
    const page = await api(`/api/operations/${op.id}?offset=${offset}&limit=200`);
    rows.append(planTable(page.entries.map(entry => ({...entry, note: entry.error || ''}))));
    offset += page.entries.length;
    more.hidden = offset >= page.done + page.failed;
  }
  content.append(rows, more);
  await load();
  await dialog(op.label, content);
}

export async function refreshOperations() {
  if (!active) return;
  const panel = $('#operations-panel');
  const operations = await api('/api/operations');
  panel.replaceChildren(el('h2', {}, 'Operations'));
  if (!operations.length) panel.append(el('p', {class: 'muted'}, 'File actions will appear here.'));
  for (const op of operations) {
    const card = el('article', {class: 'operation-card'});
    card.append(el('strong', {}, op.label), el('small', {}, dateText(op.started)),
      el('p', {}, [op.status, ...Object.entries(op.counts).filter(([action]) => action !== 'error').map(([action, count]) => `${count} ${action.replace('_', ' ')}`),
        `${op.failed} failed`].join(' · ')));
    if (op.status === 'running') card.append(el('progress', {max: Math.max(1, op.total), value: op.done + op.failed}), button('Cancel', () => post(`/api/operations/${op.id}/cancel`)));
    if (newestUndoable([op])) card.append(button('Undo', () => undoOperation(op.id)));
    if (!op.undoable && op.kind !== 'undo') card.append(el('p', {class: 'muted'}, op.kind === 'trash' || op.params?.action === 'trash' ? 'To restore these files, open the Recycle Bin.' : 'This operation cannot be undone.'));
    card.append(button('Show entries', () => showEntries(op)));
    panel.append(card);
  }
}

export function showOperations(value = true) {
  active = value;
  savePrefs({sidebar: true});
  $('#operations-panel').hidden = !value;
  for (const id of ['folder-controls', 'folder-tree', 'filter-panel', 'stats-panel']) $(`#${id}`).hidden = value;
  if (value) refreshOperations().catch(() => {});
}

export function initOperations() {
  const tabs = el('nav', {class: 'sidebar-tabs', 'aria-label': 'Library panels'});
  tabs.append(button('Folders', () => showOperations(false)), button('Filters', () => {
    showOperations(false); $('#filter-panel').scrollIntoView({block: 'nearest'});
  }), button('Operations', () => showOperations()));
  $('#sidebar').prepend(tabs);
  const panel = el('div', {id: 'operations-panel'});
  panel.hidden = true;
  $('#sidebar').append(panel);
  on('undo-operation', id => undoOperation(id).catch(() => {}));
  on('operation-start', id => watchOperation(id).catch(() => {}));
  setInterval(() => { if (active) refreshOperations().catch(() => {}); }, 2000);
}
