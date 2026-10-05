/** Wire the local library, ordering controls, and restorable navigation state. */
import './lifecycle.js';
import {$, el, api, post, dispatch, on, debounce, sizeText, toast} from './api.js';
import {state, sorts, query, defaultOrder, changeQuery, readHash, writeHash, loadPrefs, savePrefs, activeFilters} from './state.js';
import {initGrid, refreshGrid, selectionStatus} from './grid.js';
import {initViewer, openViewer, closeViewer, relocateCurrent} from './viewer.js';
import {initPanels, chooseFolder, settingsDialog} from './panels.js';
import {initKeys} from './keys.js';
import {initFilters} from './filters.js';
import {initPlayback} from './playback.js';
import {initCompare} from './compare.js';
import {initTools, breadcrumbs} from './tools_ui.js';

let roots = [];
let stats = null;
let polling = false;
let rootGeneration = 0;
const revisions = new Map();

function syncControls() {
  for (const prefix of ['', 'view-']) {
    $(`#${prefix}sort`).value = state.sort;
    $(`#${prefix}direction`).textContent = state.order === 'desc' ? '↓ desc' : '↑ asc';
    $(`#${prefix}shuffle`).hidden = state.sort !== 'random';
  }
  $('#search').value = state.filters.q || '';
  $('#roots').value = state.root || '';
  breadcrumbs();
  $('#rescan').disabled = !state.root;
  updateStatus();
}

function updateStatus() {
  $('#status').textContent = state.root ? `${state.total.toLocaleString()} images · ${sizeText(stats?.bytes || 0)} · sorted by ${sorts[state.sort]} ${state.order === 'desc' ? '↓' : '↑'} · ${activeFilters()} filters` : 'Open a folder to get started';
  selectionStatus();
}

async function refreshRoots() {
  roots = await api('/api/roots');
  const select = $('#roots');
  select.replaceChildren(el('option', {value: ''}, 'Recent folders'));
  for (const root of roots) select.append(el('option', {value: root.id}, `${root.path} (${root.count})`));
  select.value = state.root || '';
  state.rootRecord = roots.find(root => root.id === state.root) || null;
}

export async function refreshStats() {
  if (!state.root) return;
  const token = rootGeneration;
  const result = await api(`/api/roots/${state.root}/stats?${query()}`);
  if (token !== rootGeneration) return;
  stats = result;
  updateStatus();
  dispatch('stats-ready', result);
}

async function openRoot(path, recursive = true) {
  const result = await post('/api/roots', {path, recursive});
  state.root = result.root.id;
  state.rootRecord = result.root;
  state.view = 'grid';
  state.index = 0;
  state.focusIndex = 0;
  state.filters = state.prefs.dirMode === 'exact' ? {dir: result.root.path, dir_mode: 'exact'} : {};
  state.selected.clear();
  state.revision++;
  $('#viewer').hidden = true;
  writeHash();
  rootGeneration++;
  await refreshRoots();
  syncControls();
  await refreshGrid(true);
  await refreshStats();
  dispatch('root-changed');
  $('#grid').focus();
}

async function chooseAndOpen(fallback = false) {
  const choice = await chooseFolder(fallback);
  if (choice) await openRoot(choice.path, choice.recursive);
}

async function pollScan() {
  if (!state.root || polling) return;
  const root = state.root;
  polling = true;
  try {
    const job = await api(`/api/roots/${root}/scan`, {quiet: true});
    if (root !== state.root) return;
    const revisionChanged = (revisions.get(root) ?? state.rootRecord?.revision ?? 0) !== job.revision;
    revisions.set(root, job.revision);
    const wasRunning = $('#scan-progress').dataset.running === 'true';
    $('#scan-progress').dataset.running = String(job.running);
    $('#scan-progress').textContent = job.running ? `${job.phase === 'files' ? 'Indexing' : 'Reading metadata'} · ${job.seen.toLocaleString()} files${job.phase === 'metadata' ? ` · ${job.meta_done}/${job.meta_total}` : ''}` : job.error ? `Scan failed: ${job.error}` : job.watch_error || (job.prewarm_total && job.prewarm_done < job.prewarm_total ? `Warming thumbnails · ${job.prewarm_done}/${job.prewarm_total}` : '');
    if (job.running || wasRunning || revisionChanged) {
      await refreshGrid();
      await refreshStats();
      if (job.running && !state.total) setTimeout(pollScan, 200);
      if (!job.running) {
        await refreshRoots();
        if (state.view === 'viewer') await relocateCurrent();
        dispatch('scan-finished');
        if (revisionChanged) {
          $('#statusbar').classList.remove('updated-pulse');
          requestAnimationFrame(() => $('#statusbar').classList.add('updated-pulse'));
          $('#update-pulse').textContent = 'updated';
          setTimeout(() => { $('#update-pulse').textContent = ''; }, 1800);
        }
      }
    }
  } catch (error) { $('#scan-progress').textContent = error.message; }
  finally { polling = false; }
}

async function restore() {
  rootGeneration++;
  readHash();
  state.selected.clear();
  state.revision++;
  const wantedViewer = state.view === 'viewer';
  state.rootRecord = roots.find(root => root.id === state.root) || null;
  if (state.root && !state.rootRecord) { state.root = null; state.view = 'grid'; toast('That library is no longer indexed'); }
  syncControls();
  if (!wantedViewer && !$('#viewer').hidden) await closeViewer(false);
  await refreshGrid();
  if (state.root) {
    await refreshStats();
    dispatch('root-changed');
    if (wantedViewer) await openViewer(state.index, false);
  }
}

function applyPrefs() {
  $('#sidebar').hidden = !state.prefs.sidebar;
  $('#thumb-size').value = state.prefs.thumbSize;
}

async function main() {
  for (const prefix of ['', 'view-']) {
    const select = $(`#${prefix}sort`);
    for (const [key, label] of Object.entries(sorts)) select.append(el('option', {value: key}, label));
    select.onchange = () => changeQuery({sort: select.value, order: defaultOrder(select.value)});
    $(`#${prefix}direction`).onclick = () => changeQuery({order: state.order === 'asc' ? 'desc' : 'asc'});
    $(`#${prefix}shuffle`).onclick = async () => {
      if (!state.root) return;
      try { await post(`/api/roots/${state.root}/shuffle`); changeQuery({sort: 'random', order: 'asc'}); } catch { /* Already reported. */ }
    };
  }
  initGrid(); initViewer(); initPanels(); initKeys(); initFilters(); initPlayback(); initCompare(); initTools();
  applyPrefs();
  on('prefs-changed', applyPrefs);
  on('total-changed', updateStatus);
  on('library-changed', () => refreshStats().catch(() => {}));
  on('open-library', path => openRoot(path).catch(() => {}));
  on('query-changed', async () => {
    rootGeneration++;
    syncControls();
    try { await refreshGrid(true); await refreshStats(); await relocateCurrent(); } catch { /* Already reported. */ }
  });
  $('#open-folder').onclick = () => chooseAndOpen().catch(() => {});
  $('#empty-open').onclick = $('#open-folder').onclick;
  $('#browse-folder').onclick = () => chooseAndOpen(true).catch(() => {});
  $('#roots').onchange = event => {
    const root = roots.find(item => item.id === Number(event.target.value));
    if (root) openRoot(root.path, !!root.recursive).catch(() => {});
  };
  $('#search').oninput = debounce(event => {
    const filters = {...state.filters};
    if (event.target.value) filters.q = event.target.value;
    else delete filters.q;
    changeQuery({filters});
  });
  $('#rescan').onclick = () => post(`/api/roots/${state.root}/rescan`).then(pollScan).catch(() => {});
  $('#sidebar-toggle').onclick = () => savePrefs({sidebar: !state.prefs.sidebar});
  $('.brand').onclick = event => { event.preventDefault(); if (state.view === 'viewer') closeViewer().catch(() => {}); else $('#grid').focus(); };
  on('show-diagnostics', () => settingsDialog(true).catch(() => {}));
  window.addEventListener('hashchange', () => {
    if (location.hash === '#diagnostics') settingsDialog(true).catch(() => {});
    else refreshRoots().then(restore).catch(() => {});
  });
  await Promise.all([loadPrefs(), refreshRoots()]);
  const params = new URLSearchParams(location.search);
  if (params.get('open')) await openRoot(params.get('open'), params.get('recursive') !== '0');
  else await restore();
  setInterval(pollScan, 2000);
  pollScan();
  if (location.hash === '#diagnostics') settingsDialog(true).catch(() => {});
}

main().catch(error => toast(`Could not start: ${error.message}`));
