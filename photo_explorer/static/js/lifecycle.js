/** Desktop close signals remain independent of indexing and UI initialization. */
let sequence = 0;
let stopped = false;

async function heartbeat() {
  if (stopped) return;
  try {
    const response = await fetch('/api/heartbeat', {method: 'POST', keepalive: true});
    if (!response.ok) return;
    const {command} = await response.json();
    if (command && command.sequence !== sequence) {
      sequence = command.sequence;
      window.focus();
      if (command.fragment === '#diagnostics') window.dispatchEvent(new CustomEvent('show-diagnostics'));
      else if (command.fragment.startsWith('#root=')) location.hash = command.fragment;
    }
  } catch { /* Server shutdown and brief reloads need no error toast. */ }
}

heartbeat();
setInterval(heartbeat, 5000);
window.addEventListener('pageshow', () => { stopped = false; heartbeat(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) heartbeat(); });
window.addEventListener('pagehide', () => {
  stopped = true;
  navigator.sendBeacon('/api/window-closed');
});

export async function quit() {
  const response = await fetch('/api/shutdown', {method: 'POST', keepalive: true});
  if (!response.ok) throw new Error('Could not shut down Photo Explorer');
  stopped = true;
  document.body.replaceChildren(Object.assign(document.createElement('p'), {
    textContent: 'Photo Explorer has stopped. You can close this window.',
  }));
}
