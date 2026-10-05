/** Exact duplicate cards and explicit keeper-based batch resolution. */
import {el, api, post, toast, dateText, sizeText} from './api.js';
import {state} from './state.js';
import {dialog} from './panels.js';
import {button, input, line, select, destination, planTable} from './tool_forms.js';
import {watchOperation} from './operations_ui.js';

export async function duplicatesDialog() {
  if (!state.root) { toast('Open a library first'); return; }
  const root = state.root;
  const content = el('div', {class: 'tool-form'});
  const progress = el('p', {role: 'status'});
  const cards = el('div');
  const preview = el('div', {class: 'tool-preview'});
  const dest = destination('Existing destination for moved duplicates', '');
  const strategy = select('Suggested keeper', ['largest', 'newest', 'oldest', 'smallest_path']);
  const action = select('Bulk action', ['move', 'trash']);
  const confirmTrash = input('Confirm Recycle Bin action', false, 'checkbox');
  let groups = [];
  let keepers = {};
  let request = null;
  let closed = false;
  let runId = null;
  let offset = 0;

  function suggested(members) {
    return [...members].sort((a, b) => strategy.value === 'newest' ? b.mtime - a.mtime : strategy.value === 'oldest' ? a.mtime - b.mtime
      : strategy.value === 'largest' ? b.size - a.size || a.path.localeCompare(b.path) : a.path.localeCompare(b.path))[0].id;
  }
  async function prepare(selected, chosenAction) {
    if (chosenAction === 'trash' && !confirmTrash.checked) { toast('Check the Recycle Bin confirmation first'); return; }
    request = {group_ids: selected.map(group => group.id), keep: Object.fromEntries(selected.map(group => [group.id, keepers[group.id]])),
      action: chosenAction, dest: dest.control.value || null, confirm: confirmTrash.checked};
    const result = await post('/api/duplicates/preview', request);
    preview.replaceChildren(el('strong', {}, `${result.total} files · ${sizeText(result.bytes)} · ${chosenAction}`), planTable(result.entries));
    apply.disabled = !result.total;
  }
  function render() {
    cards.replaceChildren();
    for (const group of groups.filter(item => item.members.length > 1)) {
      if (!keepers[group.id]) keepers[group.id] = suggested(group.members);
      const card = el('article', {class: 'duplicate-card'});
      card.append(el('h2', {}, `Group ${group.id} · ${group.members.length} exact copies`));
      const members = el('div', {class: 'duplicate-members'});
      for (const member of group.members) {
        const keep = input(`Keep ${member.name}`, member.id === keepers[group.id], 'radio');
        keep.name = `keeper-${group.id}`;
        keep.checked = member.id === keepers[group.id];
        keep.onchange = () => { keepers[group.id] = member.id; invalidate(); };
        const box = el('label', {class: 'duplicate-member'});
        box.append(keep, el('img', {src: `/api/images/${member.id}/thumb?s=160`, alt: member.name, loading: 'lazy'}),
          el('strong', {}, member.name), el('small', {}, `${sizeText(member.size)} · ${member.width || '?'}×${member.height || '?'} · ${dateText(member.mtime)}`),
          el('small', {}, member.display_path));
        members.append(box);
      }
      card.append(members, button('Keep this, move others', () => prepare([group], 'move')),
        button('Trash others…', () => prepare([group], 'trash')));
      cards.append(card);
    }
  }
  const more = button('Load more groups', async () => load(false));
  async function load(reset = true) {
    if (reset) { offset = 0; groups = []; keepers = {}; }
    const result = await api(`/api/roots/${root}/duplicates?${new URLSearchParams({offset, limit: 50, ...(runId ? {run_id: runId} : {})})}`);
    if (closed) return;
    runId = result.run?.id || null;
    groups.push(...result.groups);
    offset += result.groups.length;
    more.hidden = offset >= result.total;
    progress.textContent = result.run ? `${result.total} groups · ${result.run.status}${result.run.error ? ` · ${result.run.error}` : ''}` : 'Scan to find exact copies.';
    render();
  }
  const scan = button('Scan exact duplicates', async () => {
    runId = (await post(`/api/roots/${root}/duplicates/scan`, {mode: 'exact'})).run_id;
    while (!closed) {
      const status = await api(`/api/roots/${root}/scan`);
      progress.textContent = `Hashing · ${status.dup_done}/${status.dup_total}`;
      if (!status.dup_running) break;
      await new Promise(resolve => setTimeout(resolve, 600));
    }
    if (!closed) await load();
  });
  const apply = button('Apply previewed resolution', async () => {
    if (!request) return;
    const result = await post('/api/duplicates/resolve', request);
    invalidate();
    await watchOperation(result.operation_id, progress);
    if (!closed) await load();
  });
  apply.disabled = true;
  function invalidate() { request = null; apply.disabled = true; }
  strategy.onchange = () => { keepers = {}; render(); invalidate(); };
  const bulk = button('Preview all groups', async () => {
    while (!more.hidden) await load(false);
    const available = groups.filter(group => group.members.length > 1);
    if (!available.length) { toast('No unresolved groups'); return; }
    await prepare(available, action.value);
  });
  content.append(el('p', {}, 'Exact matches compare every byte using SHA-256. Select the copy to keep in each group.'), scan, progress,
    line('Suggest keeper', strategy), dest.row, line('Bulk action', action),
    line('I confirm sending non-keepers to the Recycle Bin', confirmTrash), bulk, cards, more, preview, apply);
  content.addEventListener('input', invalidate);
  await load();
  await dialog('Duplicates', content);
  closed = true;
}
