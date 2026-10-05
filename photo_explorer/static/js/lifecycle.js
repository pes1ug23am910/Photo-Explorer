/** Desktop close signals remain independent of indexing and UI initialization. */
let sequence = 0;
let stopped = false;
let failures = 0;

function showStopped() {
  stopped = true;
  document.body.replaceChildren(Object.assign(document.createElement('p'), {
    textContent: 'Photo Explorer has stopped. You can close this window.',
  }));
}

async function heartbeat() {
  if (stopped) return;
  try {
    const response = await fetch('/api/heartbeat', {method: 'POST', keepalive: true});
    if (!response.ok) return;
    failures = 0;
    const {command} = await response.json();
    if (command && command.sequence !== sequence) {
      sequence = command.sequence;
      window.focus();
      if (command.fragment === '#diagnostics') window.dispatchEvent(new CustomEvent('show-diagnostics'));
      else if (command.fragment.startsWith('#root=')) location.hash = command.fragment;
    }
  } catch {
    // A brief reload must not alarm anyone, but a server that stays gone (Quit from the tray,
    // or a crash) should say so instead of leaving a frozen page behind.
    failures += 1;
    if (failures >= 3) showStopped();
  }
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
  showStopped();
}
