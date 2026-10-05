/** Saved rules and a typed condition builder with inspectable previews. */
import {el, api, post, toast, sizeText} from './api.js';
import {state} from './state.js';
import {dialog} from './panels.js';
import {button, input, line, select, destination, planTable} from './tool_forms.js';
import {serializeCondition, serializeRule} from './operation_logic.js';
import {watchOperation} from './operations_ui.js';

const operators = {
  name: ['contains', 'starts', 'ends', 'glob', 'regex', 'suffix', 'prefix'], ext: ['in'], kind: ['in'],
  size: ['lt', 'gt', 'between'], width: ['lt', 'gt', 'between'], height: ['lt', 'gt', 'between'], pixels: ['lt', 'gt', 'between'],
  orient: ['is'], mtime: ['before', 'after', 'between'], ctime: ['before', 'after', 'between'], taken: ['before', 'after', 'between'],
  rating: ['gte', 'eq'], favorite: ['is'], camera: ['contains'], meta_failed: ['is'], dir: ['is', 'under'],
};

export async function rulesDialog(path = null, ids = null) {
  if (!state.root) { toast('Open a library first'); return; }
  const content = el('div', {class: 'tool-form'});
  const preset = select('Saved preset', [['', 'New rule']]);
  const name = input('Rule name', 'Suffix -t');
  const scope = input('Scope folder', path || state.filters.dir || state.rootRecord.path);
  const dirMode = select('Folder mode', [['subtree', 'Include subfolders'], ['exact', 'This folder only']], state.prefs.dirMode);
  const others = input('Include other files', false, 'checkbox');
  if (ids) { scope.disabled = true; dirMode.disabled = true; others.disabled = true; }
  const match = select('Match conditions', [['all', 'All conditions'], ['any', 'Any condition']]);
  const rows = el('div');
  const type = select('Action', ['move', 'copy']);
  const dest = destination('Destination', `${state.rootRecord.path}\\_sorted\\Suffix -t`);
  const collision = select('Collision policy', ['rename', 'skip', 'skip_identical']);
  const createDest = input('Create destination', true, 'checkbox');
  const preview = el('div', {class: 'tool-preview'});
  const progress = el('p', {role: 'status'});
  let conditions = [];
  let presets = [];
  let expected = null;
  let previewBody = '';

  function addCondition(initial = {field: 'name', op: 'suffix', value: '-t'}) {
    const row = el('div', {class: 'condition-row'});
    const field = select('Field', Object.keys(operators), initial.field);
    const op = select('Operator', operators[field.value], initial.op);
    const value = input('Value', '');
    const upper = input('Upper value', '');
    const unit = select('Size unit', ['B', 'KB', 'MB']);
    const boolean = select('Boolean value', ['true', 'false'], String(initial.value));
    const record = {row, read: () => serializeCondition(field.value, op.value,
      ['favorite', 'meta_failed'].includes(field.value) ? boolean.value : value.value, unit.value, upper.value)};
    function sync() {
      const isBool = ['favorite', 'meta_failed'].includes(field.value);
      value.hidden = isBool;
      boolean.hidden = !isBool;
      upper.hidden = op.value !== 'between';
      unit.hidden = field.value !== 'size';
      value.type = upper.type = ['mtime', 'ctime', 'taken'].includes(field.value) ? 'datetime-local'
        : ['size', 'width', 'height', 'pixels', 'rating'].includes(field.value) ? 'number' : 'text';
      value.placeholder = op.value === 'in' ? 'Comma-separated values' : 'Value';
    }
    function display(v) {
      if (['mtime', 'ctime', 'taken'].includes(field.value) && typeof v === 'number') {
        const date = new Date(v * 1000);
        return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      }
      return v ?? '';
    }
    sync();
    value.value = Array.isArray(initial.value) ? initial.op === 'between' ? display(initial.value[0]) : initial.value.join(', ') : display(initial.value);
    upper.value = initial.op === 'between' ? display(initial.value[1]) : '';
    field.onchange = () => {
      op.replaceChildren(...operators[field.value].map(v => el('option', {value: v}, v)));
      sync(); invalidate();
    };
    op.onchange = sync;
    row.append(field, op, value, upper, boolean, unit, button('Remove', () => {
      conditions = conditions.filter(item => item !== record); row.remove(); invalidate();
    }));
    conditions.push(record);
    rows.append(row);
  }

  function read() {
    return serializeRule({name: name.value, root: state.root, dir: scope.value, dirMode: dirMode.value,
      others: others.checked, ids, match: match.value, conditions: conditions.map(row => row.read()),
      type: type.value, dest: dest.control.value, collision: collision.value, createDest: createDest.checked});
  }
  function invalidate() { expected = null; run.disabled = true; }
  async function refreshPresets(selected = '') {
    presets = await api('/api/rules');
    preset.replaceChildren(el('option', {value: ''}, 'New rule'), ...presets.map(row => el('option', {value: row.id}, row.name)));
    preset.value = String(selected);
  }
  preset.onchange = () => {
    const saved = presets.find(row => row.id === Number(preset.value));
    if (!saved) return;
    const rule = saved.body;
    name.value = rule.name;
    const sameRoot = rule.scope.root_id === state.root;
    scope.value = sameRoot && rule.scope.dir ? rule.scope.dir : path || state.filters.dir || state.rootRecord.path;
    dirMode.value = rule.scope.dir_mode || 'subtree';
    others.checked = !!rule.scope.include_other_files;
    match.value = rule.match;
    type.value = rule.action.type;
    dest.control.value = rule.action.dest || `${state.rootRecord.path}\\_sorted\\${rule.name.replace(/[<>:"/\\|?*]/g, '_')}`;
    collision.value = rule.action.collision;
    createDest.checked = rule.action.create_dest;
    rows.replaceChildren(); conditions = [];
    rule.conditions.forEach(addCondition);
    invalidate();
  };
  const previewButton = button('Preview', async () => {
    const rule = read();
    const result = await post('/api/rules/preview', {rule});
    expected = result.total;
    previewBody = JSON.stringify(rule);
    preview.replaceChildren(el('p', {}, `${result.total} matches · ${sizeText(result.bytes)}${result.total > 2000 ? ' · showing first 2000' : ''}`), planTable(result.entries, true));
    run.disabled = !result.total;
  });
  const run = button('Run', async () => {
    const rule = read();
    if (expected === null || previewBody !== JSON.stringify(rule)) { invalidate(); toast('Preview this rule before running'); return; }
    const result = await post(preset.value ? `/api/rules/${preset.value}/run` : '/api/rules/run', {rule, expected_total: expected});
    invalidate();
    await watchOperation(result.operation_id, progress);
  });
  run.disabled = true;
  const controls = el('div', {class: 'tool-actions'});
  controls.append(previewButton, run, button('Save as preset', async () => {
    const saved = await post('/api/rules', read()); await refreshPresets(saved.id); toast('Preset saved', true);
  }), button('Update preset', async () => {
    if (!preset.value) return;
    const id = preset.value;
    await api(`/api/rules/${id}`, {method: 'PUT', body: read()}); await refreshPresets(id);
  }), button('Duplicate preset', async () => {
    const rule = read(); rule.name += ' copy';
    const saved = await post('/api/rules', rule); await refreshPresets(saved.id); preset.onchange();
  }), button('Delete preset', async () => {
    if (!preset.value) return;
    await api(`/api/rules/${preset.value}`, {method: 'DELETE'}); await refreshPresets();
  }));
  content.append(line('Preset', preset), line('Name', name), line(ids ? `${ids.length} selected images in folder` : 'Scope', scope),
    line('Folder mode', dirMode), line('Include other files', others), line('Match', match), rows,
    button('Add condition', () => { addCondition(); invalidate(); }), line('Action', type), dest.row,
    line('Collision', collision), line('Create destination', createDest), controls, progress, preview);
  content.addEventListener('input', invalidate);
  content.addEventListener('change', invalidate);
  addCondition();
  await refreshPresets();
  await dialog(ids ? 'Apply rule to selection' : 'Rules', content);
}
