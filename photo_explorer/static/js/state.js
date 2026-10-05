/** Shared query state; every grid and viewer request reads this definition. */
import {api, dispatch, debounce, dateText, sizeText} from './api.js';

export const sorts = {
  mtime: 'Date modified', ctime: 'Date created', taken: 'Date taken', name: 'Name',
  path: 'Folder + name', size: 'Size', pixels: 'Resolution', width: 'Width', height: 'Height',
  aspect: 'Aspect ratio', ext: 'Type', camera: 'Camera', rating: 'Rating', random: 'Random',
};
export const filterKeys = ['ext', 'q', 'date_field', 'date_from', 'date_to', 'size_min', 'size_max',
  'orient', 'min_rating', 'favorites', 'camera', 'dir', 'dir_mode', 'animated', 'has_gps', 'min_pixels', 'meta_failed'];
const defaults = {thumbSize: 200, sidebar: false, info: false, wrap: false, upscale: false,
  skipConfirm: false, wheelMode: 'navigate', theme: 'dark', groupDates: false, filmstrip: true,
  lockZoom: false, lockRotation: false, slideInterval: 4, slideshowLoop: false, fade: true, dirMode: 'subtree',
  cacheMaxMB: 2048, prewarm: true, watchFolders: true};
let localPrefs = {};
try { localPrefs = JSON.parse(localStorage.getItem('photo-explorer') || '{}'); } catch { /* Storage may be disabled. */ }
export const state = {root: null, rootRecord: null, sort: 'mtime', order: 'desc', filters: {}, total: 0,
  view: 'grid', index: 0, current: null, selected: new Set(), focusIndex: 0, prefs: {...defaults, ...localPrefs}, revision: 0};

export function query(extra = {}) {
  return new URLSearchParams({sort: state.sort, order: state.order, ...state.filters, ...extra}).toString();
}
export function listURL(extra = {}) { return `/api/roots/${state.root}/images?${query(extra)}`; }
export function activeFilters() { return Object.entries(state.filters).filter(([key, value]) => key !== 'date_field' && value !== '').length; }
export function writeHash(push = false) {
  if (!state.root) return;
  const hash = new URLSearchParams({root: state.root, sort: state.sort, order: state.order,
    ...state.filters, view: state.view, i: state.index});
  history[push ? 'pushState' : 'replaceState'](null, '', `${location.pathname}#${hash}`);
}
export function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const root = Number(params.get('root'));
  state.root = root > 0 ? root : null;
  state.sort = Object.hasOwn(sorts, params.get('sort')) ? params.get('sort') : 'mtime';
  state.order = ['asc', 'desc'].includes(params.get('order')) ? params.get('order') : defaultOrder(state.sort);
  state.filters = Object.fromEntries(filterKeys.filter(key => params.get(key)).map(key => [key, params.get(key)]));
  state.view = params.get('view') === 'viewer' ? 'viewer' : 'grid';
  state.index = Math.max(0, Number(params.get('i')) || 0);
}
export function defaultOrder(sort) { return ['mtime', 'ctime', 'taken'].includes(sort) ? 'desc' : 'asc'; }
export function changeQuery(values) {
  Object.assign(state, values);
  state.revision++;
  state.selected.clear();
  writeHash();
  dispatch('query-changed');
}
const persist = debounce(() => api('/api/settings', {method: 'PUT', body: state.prefs}).catch(() => {}), 400);
export function savePrefs(values) {
  Object.assign(state.prefs, values);
  try { localStorage.setItem('photo-explorer', JSON.stringify(state.prefs)); } catch { /* Server storage remains available. */ }
  persist();
  dispatch('prefs-changed');
}
export async function loadPrefs() {
  const stored = await api('/api/settings');
  Object.assign(state.prefs, stored);
  dispatch('prefs-changed');
}
export function sortValue(item, index = 0) {
  if (!item) return '';
  switch (state.sort) {
    case 'mtime': return `Modified ${dateText(item.mtime)}`;
    case 'ctime': return `Created ${dateText(item.ctime)}`;
    case 'taken': return `${item.taken ? 'Taken' : 'Modified (fallback)'} ${dateText(item.taken || item.mtime)}`;
    case 'size': return sizeText(item.size);
    case 'pixels': return item.width ? `${item.width}×${item.height} (${(item.width * item.height / 1e6).toFixed(1)} MP)` : 'Resolution unavailable';
    case 'width': case 'height': return `${item[state.sort] ?? '—'} px`;
    case 'aspect': return item.aspect ? `${item.aspect.toFixed(3)} : 1` : 'Aspect unavailable';
    case 'path': return `${item.dir} / ${item.name}`;
    case 'camera': return item.camera || 'Camera unavailable';
    case 'rating': return `${item.rating} / 5 stars`;
    case 'random': return `#${index + 1} of shuffle`;
    case 'ext': return item.ext.toUpperCase();
    default: return item.name;
  }
}
