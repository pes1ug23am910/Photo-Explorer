/** Library maintenance preferences without changing viewer controls. */
import {el, api, post, sizeText, toast} from './api.js';
import {state} from './state.js';
import {input, line, button} from './tool_forms.js';

export async function cacheSettings(content) {
  const cap = input('Cache cap (MB)', state.prefs.cacheMaxMB ?? 2048, 'range');
  cap.min = 256; cap.max = 16384; cap.step = 256;
  // A range input clamps a value assigned before its bounds to the default 0–100 range, so set it again now.
  cap.value = state.prefs.cacheMaxMB ?? 2048;
  const label = el('span', {}, `${cap.value} MB`);
  cap.oninput = () => { label.textContent = `${cap.value} MB`; };
  const prewarm = input('Pre-warm thumbnails when idle', state.prefs.prewarm ?? true, 'checkbox');
  const watch = input('Watch folders for changes', state.prefs.watchFolders ?? true, 'checkbox');
  const usage = el('p');
  const bar = el('progress');
  async function refresh() {
    const stats = await api('/api/cache');
    usage.textContent = `${sizeText(stats.bytes)} / ${sizeText(stats.cap_bytes)} · ${stats.files} files`;
    bar.max = Math.max(1, stats.cap_bytes); bar.value = stats.bytes;
  }
  const health = await api('/api/health');
  content.append(el('h2', {}, 'Cache'), line('Maximum cache size', cap, label), usage, bar,
    button('Clear cache', async () => { await post('/api/cache/clear'); await refresh(); }), line('Pre-warm', prewarm),
    el('h2', {}, 'Library'), line('Watch folders', watch),
    el('p', {class: 'muted'}, health.last_backup ? `Latest index backup: ${health.last_backup} · newest 7 days kept` : 'No index backup available'));
  await refresh();
  await diagnosticsSettings(content);
  return () => ({cacheMaxMB: Number(cap.value), prewarm: prewarm.checked, watchFolders: watch.checked});
}

async function diagnosticsSettings(content) {
  const info = await api('/api/diagnostics');
  const section = el('section', {id: 'diagnostics-section'});
  section.append(el('h2', {}, 'Diagnostics'));
  const details = el('dl', {class: 'diagnostics-details'});
  for (const [label, value] of [
    ['Version', `${info.version} · ${info.frozen ? 'Desktop build' : 'Source'} · Python ${info.python}`],
    ['Home', info.home], ['Log file', info.log_path],
    ['Library', `${info.roots} folders · ${info.images.toLocaleString()} images`],
    ['Cache usage', `${sizeText(info.cache.bytes)} / ${sizeText(info.cache.cap_bytes)} · ${info.cache.files} files`],
    ['Watcher', info.watcher.error || info.watcher.mode || 'Not running'],
  ]) details.append(el('dt', {}, label), el('dd', {}, String(value)));
  const actions = el('div', {class: 'tool-actions'});
  actions.append(button('Open log folder', () => post('/api/diagnostics/open-log-folder')),
    button('Copy diagnostics', async () => {
      const current = await api('/api/diagnostics');
      await navigator.clipboard.writeText(JSON.stringify(current, null, 2));
      toast('Diagnostics copied', true);
    }));
  section.append(details, actions);
  content.append(section);
}
