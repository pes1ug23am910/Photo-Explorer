/** Local JSON requests and compact DOM helpers. */
export const $ = (selector, parent = document) => parent.querySelector(selector);
export const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];

export function el(tag, attributes = {}, text = '') {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === 'class') node.className = value;
    else node.setAttribute(key, String(value));
  }
  node.textContent = text;
  return node;
}

export function toast(message, success = false) {
  const node = el('div', {class: `toast${success ? ' success' : ''}`, role: 'status'}, message);
  $('#toasts').append(node);
  setTimeout(() => node.remove(), 6500);
}

export async function api(path, options = {}) {
  try {
    const response = await fetch(path, {
      ...options,
      headers: {'Content-Type': 'application/json', ...options.headers},
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({detail: response.statusText}));
      const detail = Array.isArray(error.detail) ? error.detail.map(item => item.msg).join('; ') : error.detail;
      const failure = new Error(detail || `Request failed (${response.status})`);
      failure.status = response.status;
      throw failure;
    }
    return await response.json();
  } catch (error) {
    if (error.name !== 'AbortError' && !options.quiet) toast(error.message);
    throw error;
  }
}

export const post = (path, body = {}) => api(path, {method: 'POST', body});
export const dispatch = (type, detail) => window.dispatchEvent(new CustomEvent(type, {detail}));
export const on = (type, handler) => window.addEventListener(type, event => handler(event.detail));

export function dateText(value) {
  if (!value) return '—';
  const d = new Date(value * 1000);
  const pad = number => String(number).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function sizeText(value = 0) {
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let number = value / 1024;
  let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index++; }
  return `${number.toFixed(1)} ${units[index]}`;
}

export function debounce(callback, delay = 250) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => callback(...args), delay); };
}
