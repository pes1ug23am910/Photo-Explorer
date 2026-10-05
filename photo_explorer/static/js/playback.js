/** Filmstrip, slideshow, clipboard, and presentation preferences. */
import {$, el, api, on, toast} from './api.js';
import {state, listURL, savePrefs, sortValue} from './state.js';
import {navigate, applyMode, rotateView} from './viewer.js';

export const slideshow = {active: false, paused: false};
let filmToken = 0;
let frame = 0;
let started = 0;
let elapsed = 0;
let advancing = false;
let wakeLock = null;

async function filmstrip() {
  const token = ++filmToken;
  const strip = $('#filmstrip');
  strip.hidden = !state.prefs.filmstrip || state.view !== 'viewer';
  $('#viewer').classList.toggle('has-filmstrip', !strip.hidden);
  if (strip.hidden || !state.root) return;
  const offset = Math.max(0, state.index - 15);
  const page = await api(listURL({offset, limit: 31}));
  if (token !== filmToken || state.view !== 'viewer') return;
  strip.replaceChildren(...page.items.map((item, local) => {
    const index = offset + local;
    const button = el('button', {class: index === state.index ? 'current' : '', title: `${item.name} · ${sortValue(item, index)}`,
      'aria-label': `Image ${index + 1}: ${item.name}`, 'aria-current': index === state.index ? 'true' : 'false'});
    button.append(el('img', {src: `/api/images/${item.id}/thumb?s=160&v=${item.mtime}-${item.size}`, alt: '', loading: 'lazy', decoding: 'async'}));
    button.onclick = () => navigate(index).catch(() => {});
    return button;
  }));
  const current = $('.current', strip);
  if (current) strip.scrollLeft = current.offsetLeft - (strip.clientWidth - current.offsetWidth) / 2;
}

async function acquireWakeLock() {
  if (!navigator.wakeLock || document.visibilityState === 'hidden') return;
  try {
    const lock = await navigator.wakeLock.request('screen');
    if (slideshow.active && !slideshow.paused) wakeLock = lock;
    else await lock.release();
  } catch { /* Unsupported or denied wake locks do not interrupt playback. */ }
}

function releaseWakeLock() {
  wakeLock?.release().catch(() => {});
  wakeLock = null;
}

function syncPlayback() {
  $('#slideshow-button').textContent = slideshow.active ? slideshow.paused ? 'Resume' : 'Pause' : 'Slideshow';
  $('#slideshow-button').setAttribute('aria-pressed', String(slideshow.active));
  $('#viewer').classList.toggle('slide-fade', slideshow.active && state.prefs.fade);
}

async function tick(now) {
  if (!slideshow.active) return;
  const duration = Math.max(1, Math.min(60, Number(state.prefs.slideInterval) || 4)) * 1000;
  if (!slideshow.paused && !advancing) {
    const progress = elapsed + now - started;
    $('#slideshow-progress').style.width = `${Math.min(100, progress / duration * 100)}%`;
    if (progress >= duration) {
      advancing = true;
      try {
        if (state.index >= state.total - 1) {
          if (state.prefs.slideshowLoop) await navigate(0);
          else { stopSlideshow(); return; }
        } else await navigate(1, true);
      } catch { stopSlideshow(); return; }
      finally { advancing = false; started = performance.now(); elapsed = 0; }
    }
  }
  frame = requestAnimationFrame(tick);
}

export function stopSlideshow() {
  slideshow.active = false;
  slideshow.paused = false;
  cancelAnimationFrame(frame);
  $('#slideshow-progress').style.width = '0%';
  releaseWakeLock();
  syncPlayback();
}

export function toggleSlideshow() {
  if (!slideshow.active) {
    slideshow.active = true;
    slideshow.paused = false;
    elapsed = 0;
    started = performance.now();
    acquireWakeLock();
    frame = requestAnimationFrame(tick);
  } else {
    slideshow.paused = !slideshow.paused;
    if (slideshow.paused) { elapsed += performance.now() - started; releaseWakeLock(); }
    else { started = performance.now(); acquireWakeLock(); }
  }
  syncPlayback();
}

export async function copyPath() {
  if (!state.current) return;
  const image = await api(`/api/images/${state.current.id}`);
  try { await navigator.clipboard.writeText(image.display_path); toast('Path copied', true); }
  catch (error) { toast(`Clipboard: ${error.message}`); }
}

export async function copyImage() {
  if (!state.current) return;
  if (!navigator.clipboard?.write || !window.ClipboardItem) { toast('Image clipboard is unavailable in this browser'); return; }
  const id = state.current.id;
  const promise = new Promise((resolve, reject) => {
    const source = new Image();
    source.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = source.naturalWidth;
      canvas.height = source.naturalHeight;
      const context = canvas.getContext('2d');
      context.drawImage(source, 0, 0);
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Cannot encode clipboard image')), 'image/png');
    };
    source.onerror = () => reject(new Error('Cannot load image for clipboard'));
    source.src = `/api/images/${id}/file?v=${state.current.mtime}-${state.current.size}`;
  });
  try { await navigator.clipboard.write([new ClipboardItem({'image/png': promise})]); toast('Image copied as PNG', true); }
  catch (error) { toast(`Clipboard: ${error.message}`); }
}

export function initPlayback() {
  const play = el('button', {id: 'slideshow-button', title: 'Slideshow (S)'}, 'Slideshow');
  play.onclick = toggleSlideshow;
  const strip = el('button', {id: 'filmstrip-button', title: 'Filmstrip (T)'}, 'Filmstrip');
  strip.onclick = () => savePrefs({filmstrip: !state.prefs.filmstrip});
  const fill = el('button', {title: 'Fill viewport (Shift+F)'}, 'Fill');
  fill.onclick = () => applyMode('fill');
  const lock = el('button', {id: 'zoom-lock', title: 'Keep zoom across images (L)'}, 'Lock zoom');
  lock.onclick = () => savePrefs({lockZoom: !state.prefs.lockZoom});
  const rotate = el('button', {title: 'Rotate view clockwise (R)'}, '↻');
  rotate.onclick = () => rotateView(90);
  $('#view-top').append(play, strip, fill, lock, rotate);
  const sync = () => {
    $('#zoom-lock').setAttribute('aria-pressed', String(!!state.prefs.lockZoom));
    $('#filmstrip-button').setAttribute('aria-pressed', String(!!state.prefs.filmstrip));
    filmstrip().catch(() => {});
  };
  on('viewer-item', () => { started = performance.now(); elapsed = 0; filmstrip().catch(() => {}); });
  on('viewer-closed', () => { filmToken++; stopSlideshow(); });
  on('prefs-changed', sync);
  on('dialog-open', () => { if (slideshow.active && !slideshow.paused) toggleSlideshow(); });
  document.addEventListener('visibilitychange', () => {
    if (slideshow.active && !slideshow.paused && document.visibilityState === 'visible') acquireWakeLock();
  });
  sync();
}
