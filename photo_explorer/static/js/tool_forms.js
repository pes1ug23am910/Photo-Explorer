/** Small DOM controls for local operation dialogs. */
import {el, post, api, toast} from './api.js';

export function input(label, value = '', type = 'text') {
  const control = el('input', {type, value, 'aria-label': label});
  if (type === 'checkbox') control.checked = !!value;
  return control;
}

export function select(label, values, chosen) {
  const control = el('select', {'aria-label': label});
  for (const value of values) control.append(el('option', {value: Array.isArray(value) ? value[0] : value}, Array.isArray(value) ? value[1] : value));
  control.value = chosen ?? (Array.isArray(values[0]) ? values[0][0] : values[0]);
  return control;
}

export function line(label, ...controls) {
  const row = el('label', {class: 'tool-field'}, label);
  row.append(...controls);
  return row;
}

export function button(label, handler) {
  const control = el('button', {type: 'button'}, label);
  control.onclick = async () => {
    control.disabled = true;
    try { await handler(); } catch (error) { if (!error.status) toast(error.message); }
    finally { control.disabled = false; }
  };
  return control;
}

export function destination(label, value) {
  const control = input(label, value);
  const browse = button('Browse…', async () => {
    const result = await post('/api/fs/pick');
    if (result.path) { control.value = result.path; control.dispatchEvent(new Event('input', {bubbles: true})); }
  });
  const row = line(label, control, browse);
  api('/api/destinations', {quiet: true}).then(items => {
    if (!items.length) return;
    const recent = select('Recent destinations', [['', 'Recent destinations…'], ...items.map(item => [item.path, item.path])]);
    recent.onchange = () => { if (recent.value) { control.value = recent.value; control.dispatchEvent(new Event('input', {bubbles: true})); } };
    row.append(recent);
  }).catch(() => {});
  return {row, control};
}

export function planTable(entries, imagePreview = false) {
  const table = el('table', {class: 'plan-table'});
  const head = el('tr');
  for (const label of ['Source', 'Destination', 'Action', 'Note']) head.append(el('th', {}, label));
  table.append(head);
  for (const entry of entries) {
    const row = el('tr');
    const source = el('td', {}, entry.path || entry.src);
    if (imagePreview && entry.image_id) source.prepend(el('img', {src: `/api/images/${entry.image_id}/thumb?s=160`, alt: '', loading: 'lazy', class: 'plan-thumb'}));
    row.append(source, el('td', {}, entry.dest_name || entry.dest?.split(/[\\/]/).at(-1) || '—'),
      el('td', {}, entry.action), el('td', {}, entry.note || ''));
    table.append(row);
  }
  return table;
}
