/** Folder tree, declarative filters, and local library aggregates. */
import {$, el, api, dispatch, on, sizeText, debounce} from './api.js';
import {state, changeQuery, savePrefs, activeFilters} from './state.js';

let extensions = [];
let panelToken = 0;

function setFilter(key, value) {
  const filters = {...state.filters};
  if (value !== '' && value !== false && value !== null) filters[key] = String(value);
  else delete filters[key];
  changeQuery({filters});
}

function buildFilters() {
  const panel = $('#filter-panel');
  panel.replaceChildren(el('h2', {}, 'Filters'));
  const types = el('fieldset');
  types.append(el('legend', {}, 'File types'));
  for (const extension of extensions) {
    const label = el('label');
    const box = el('input', {type: 'checkbox', value: extension});
    box.checked = (state.filters.ext || '').split(',').includes(extension);
    box.onchange = () => {
      const selected = [...types.querySelectorAll('input:checked')].map(input => input.value);
      setFilter('ext', selected.join(','));
    };
    label.append(box, document.createTextNode(extension.toUpperCase()));
    types.append(label);
  }
  panel.append(types);
  for (const [key, label, options] of [
    ['date_field', 'Date field', [['mtime', 'Modified'], ['ctime', 'Created'], ['taken', 'Taken / modified']]],
    ['orient', 'Orientation', [['', 'All'], ['landscape', 'Landscape'], ['portrait', 'Portrait'], ['square', 'Square']]],
    ['min_rating', 'Minimum rating', [['', 'Any'], ...[1, 2, 3, 4, 5].map(value => [String(value), `${value} ★`])]],
  ]) {
    const select = el('select', {'aria-label': label});
    for (const [value, text] of options) select.append(el('option', {value}, text));
    select.value = state.filters[key] || (key === 'date_field' ? 'mtime' : '');
    select.onchange = () => setFilter(key, select.value);
    const line = el('label', {class: 'filter-line'}, label);
    line.append(select);
    panel.append(line);
  }
  for (const [key, label, type, divisor] of [
    ['date_from', 'From', 'date', 1], ['date_to', 'Through', 'date', 1],
    ['size_min', 'Min size (MB)', 'number', 1048576], ['size_max', 'Max size (MB)', 'number', 1048576],
    ['min_pixels', 'Min megapixels', 'number', 1000000], ['camera', 'Camera contains', 'text', 1],
  ]) {
    const input = el('input', {type, 'aria-label': label});
    if (type === 'number') { input.min = 0; input.step = '.1'; }
    input.value = state.filters[key] ? (type === 'number' ? Number(state.filters[key]) / divisor : state.filters[key]) : '';
    const apply = () => setFilter(key, input.value === '' ? '' : type === 'number' ? Math.round(Number(input.value) * divisor) : input.value);
    input.addEventListener(type === 'text' ? 'input' : 'change', type === 'text' ? debounce(apply) : apply);
    const line = el('label', {class: 'filter-line'}, label);
    line.append(input);
    panel.append(line);
  }
  for (const [key, label] of [['favorites', 'Favourites only'], ['animated', 'Animated only'],
    ['has_gps', 'Has GPS coordinates'], ['meta_failed', 'Metadata failed']]) {
    const box = el('input', {type: 'checkbox'});
    box.checked = state.filters[key] === '1';
    box.onchange = () => setFilter(key, box.checked ? '1' : '');
    const line = el('label', {class: 'filter-check'});
    line.append(box, document.createTextNode(label));
    panel.append(line);
  }
  const reset = el('button', {}, 'Clear all filters');
  reset.onclick = () => changeQuery({filters: {}});
  panel.append(reset);
}

async function refreshTree() {
  const token = ++panelToken;
  if (!state.root) return;
  const root = state.root;
  const [tree, stats] = await Promise.all([api(`/api/roots/${root}/dirs`), api(`/api/roots/${root}/stats`)]);
  if (token !== panelToken) return;
  extensions = Object.keys(stats.by_ext).sort();
  const target = $('#folder-tree');
  target.replaceChildren();
  function branch(node, depth) {
    const button = el('button', {class: 'tree-folder', title: `${node.path} · ${node.count} images including subfolders`, 'aria-pressed': state.filters.dir === node.path}, node.name);
    button.append(el('small', {}, `${node.images_direct} images · ${node.files_direct} files · ${node.subfolders} folders`));
    button.style.paddingLeft = `${depth * 14 + 8}px`;
    button.onclick = () => dispatch('folder-open', node.path);
    button.oncontextmenu = event => { event.preventDefault(); dispatch('folder-context', {x: event.clientX, y: event.clientY, path: node.path}); };
    target.append(button);
    for (const child of node.children) branch(child, depth + 1);
  }
  tree.forEach(node => branch(node, 0));
  buildFilters();
}

function renderStats(stats) {
  const panel = $('#stats-panel');
  panel.replaceChildren(el('h2', {}, 'This view'), el('p', {}, `${stats.total.toLocaleString()} images · ${sizeText(stats.bytes)}`));
  panel.append(el('p', {class: 'muted'}, `${stats.rated} rated · ${stats.favorites} favourites`));
  const months = stats.by_month.filter(month => month.ym).slice(-12);
  const maximum = Math.max(1, ...months.map(month => month.n));
  const strip = el('div', {class: 'month-strip', 'aria-label': 'Images by month'});
  for (const month of months) {
    const button = el('button', {title: `${month.ym}: ${month.n} images`, 'aria-label': `Filter ${month.ym}, ${month.n} images`});
    const bar = el('span');
    bar.style.height = `${Math.max(3, month.n / maximum * 56)}px`;
    button.append(bar, el('small', {}, month.ym.slice(5)));
    button.onclick = () => {
      const [year, number] = month.ym.split('-').map(Number);
      const end = new Date(year, number, 0).getDate();
      changeQuery({filters: {...state.filters, date_from: `${month.ym}-01`, date_to: `${month.ym}-${end}`}});
    };
    strip.append(button);
  }
  panel.append(strip);
  if (months.length) panel.append(el('small', {}, `${months[0].ym} — ${months.at(-1).ym}`));
}

function syncFilters() {
  $('#filters-toggle').textContent = `Filters${activeFilters() ? ` (${activeFilters()})` : ''}`;
  $('#group-dates').setAttribute('aria-pressed', String(!!state.prefs.groupDates));
  $('#group-dates').disabled = !['mtime', 'ctime', 'taken'].includes(state.sort);
  document.documentElement.dataset.theme = state.prefs.theme;
  $('#theme').textContent = state.prefs.theme === 'light' ? 'Dark' : 'Light';
  document.documentElement.style.setProperty('--cell-fit', state.prefs.fitCells ? 'contain' : 'cover');
  $('#fit-cells').setAttribute('aria-pressed', String(!!state.prefs.fitCells));
}

export function initFilters() {
  const filterButton = el('button', {id: 'filters-toggle'}, 'Filters');
  filterButton.onclick = () => { savePrefs({sidebar: true}); $('#filter-panel').scrollIntoView({block: 'nearest'}); };
  const grouping = el('button', {id: 'group-dates', title: 'Group by day for date sorts'}, 'Group dates');
  grouping.onclick = () => {
    savePrefs({groupDates: !state.prefs.groupDates});
    dispatch('query-changed');
  };
  const theme = el('button', {id: 'theme'}, 'Light');
  theme.onclick = () => savePrefs({theme: state.prefs.theme === 'light' ? 'dark' : 'light'});
  $('#topbar').insertBefore(filterButton, $('#search'));
  $('#topbar').insertBefore(grouping, $('#rescan'));
  $('#topbar').insertBefore(theme, $('#help'));
  const fitCells = el('button', {id: 'fit-cells', title: 'Letterbox thumbnails instead of cropping'}, 'Fit cells');
  fitCells.onclick = () => savePrefs({fitCells: !state.prefs.fitCells});
  $('#topbar').insertBefore(fitCells, $('#rescan'));
  on('root-changed', () => refreshTree().catch(() => {}));
  on('scan-finished', () => refreshTree().catch(() => {}));
  on('library-changed', () => refreshTree().catch(() => {}));
  on('stats-ready', renderStats);
  on('query-changed', () => {
    syncFilters();
    // Preserve a focused camera input while its debounced query is running.
    if (!$('#filter-panel').contains(document.activeElement)) buildFilters();
  });
  on('prefs-changed', syncFilters);
  syncFilters();
}
