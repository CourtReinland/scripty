// Scripty desktop shell.
//
// Spawns the Python backend (`scripty serve`) on a free localhost port,
// waits for it to answer, then opens the dashboard in a BrowserWindow.
// `electron . --smoke` runs headless: spawn backend, health-poll, print
// "SMOKE OK", tear down, exit 0 (any failure exits 1).
'use strict';

const { app, BrowserWindow, Menu, dialog, net } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const nodeNet = require('net');
const os = require('os');
const path = require('path');

const SMOKE = process.argv.includes('--smoke');
const DEFAULT_BACKEND_BIN = '/Users/blue/Projects/scripty/.venv/bin/scripty';
const VIDEO_EXTS = ['mp4', 'mov', 'm4v', 'mkv', 'avi', 'webm'];
const SCRIPT_EXTS = ['fountain', 'txt'];
const HEALTH_TIMEOUT_MS = 10000;

let backendChild = null;
let backendPort = null;
let mainWindow = null;
let tearingDown = false;

// ---- backend process ------------------------------------------------------

function backendBin() {
  return process.env.SCRIPTY_BACKEND_BIN || DEFAULT_BACKEND_BIN;
}

function backendAlive() {
  return backendChild !== null
    && backendChild.exitCode === null
    && backendChild.signalCode === null;
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const srv = nodeNet.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function spawnBackend(bin, port) {
  const env = { ...process.env };
  if (!env.SCRIPTY_HOME) env.SCRIPTY_HOME = path.join(os.homedir(), '.scripty');
  const child = spawn(
    bin, ['serve', '--host', '127.0.0.1', '--port', String(port)],
    { env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`));
  child.stderr.on('data', (d) => process.stderr.write(`[backend] ${d}`));
  child.on('error', (err) => console.error(`[backend] spawn error: ${err}`));
  child.on('exit', (code, signal) => {
    console.log(`[backend] exited code=${code} signal=${signal}`);
  });
  return child;
}

function healthOnce(port) {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: '/api/projects', timeout: 1500 },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

async function waitForBackend(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!backendAlive()) throw new Error('backend process exited during startup');
    if (await healthOnce(port)) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`backend did not answer on 127.0.0.1:${port} within ${timeoutMs}ms`);
}

function terminateBackend() {
  return new Promise((resolve) => {
    if (!backendAlive()) { resolve(); return; }
    const child = backendChild;
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    const killTimer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
    }, 3000);
    const guardTimer = setTimeout(finish, 4000);
    killTimer.unref?.(); guardTimer.unref?.();
    child.once('exit', () => {
      clearTimeout(killTimer);
      clearTimeout(guardTimer);
      finish();
    });
    try { child.kill('SIGTERM'); } catch { clearTimeout(killTimer); finish(); }
  });
}

// ---- error window ---------------------------------------------------------

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showErrorWindow(title, lines) {
  const win = new BrowserWindow({
    width: 760, height: 460, backgroundColor: '#141414',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event) => event.preventDefault());
  const body = lines.map((l) => `<p>${escapeHtml(l)}</p>`).join('\n');
  const html = [
    '<meta charset="utf-8">',
    '<style>body{background:#141414;color:#e8e6df;font:14px/1.6 ui-monospace,Menlo,monospace;',
    'padding:40px 48px}h1{font-size:18px;color:#f2f2ee;letter-spacing:.08em}',
    'p{margin:10px 0;color:#b9b6ac;white-space:pre-wrap}</style>',
    `<h1>${escapeHtml(title)}</h1>`,
    body,
  ].join('\n');
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
  return win;
}

function showBackendMissingWindow(bin) {
  showErrorWindow('SCRIPTY BACKEND NOT FOUND', [
    `Expected the backend CLI at:\n  ${bin}`,
    'To fix, either:',
    '1. Install the backend:\n   cd /Users/blue/Projects/scripty && .venv/bin/pip install -e .',
    '2. Or set SCRIPTY_BACKEND_BIN to your scripty executable and relaunch:\n   SCRIPTY_BACKEND_BIN=/path/to/scripty open -a Scripty',
  ]);
}

// ---- API calls from main --------------------------------------------------

async function apiPost(apiPath, body) {
  const res = await net.fetch(`http://127.0.0.1:${backendPort}${apiPath}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const parsed = await res.json();
      if (parsed && parsed.detail) detail += `: ${JSON.stringify(parsed.detail)}`;
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.json();
}

// ---- menu -----------------------------------------------------------------

async function openFilm() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Open Film',
    properties: ['openFile'],
    filters: [{ name: 'Film', extensions: VIDEO_EXTS }],
  });
  if (result.canceled || result.filePaths.length === 0) return;
  try {
    await apiPost('/api/projects/from-path', { path: result.filePaths[0], name: null });
    if (mainWindow) mainWindow.webContents.reload();
  } catch (err) {
    dialog.showErrorBox('Open Film failed', String((err && err.message) || err));
  }
}

async function linkKnownScript() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Link Known Script',
    properties: ['openFile'],
    filters: [{ name: 'Script', extensions: SCRIPT_EXTS }],
  });
  if (result.canceled || result.filePaths.length === 0) return;
  if (mainWindow) {
    mainWindow.webContents.send('scripty:link-script', result.filePaths[0]);
  }
}

function buildMenu() {
  const template = [
    { role: 'appMenu' },
    {
      label: 'File',
      submenu: [
        { label: 'Open Film…', accelerator: 'CmdOrCtrl+O', click: openFilm },
        { label: 'Link Known Script…', accelerator: 'CmdOrCtrl+Shift+O', click: linkKnownScript },
        { type: 'separator' },
        { role: 'close' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---- windows --------------------------------------------------------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1200,
    minHeight: 760,
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#141414',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // OS-sandbox the renderer + preload; the preload only needs
      // contextBridge/ipcRenderer/webUtils, all sandbox-safe.
      sandbox: true,
    },
  });
  mainWindow.on('closed', () => { mainWindow = null; });
  // Electron security checklist: never let the trusted chrome-less window
  // leave the local backend origin (e.g. a URL dragged onto the window
  // triggers Chromium's default drop-navigation), and never let anything
  // open a new BrowserWindow that would inherit these webPreferences.
  const appOrigin = `http://127.0.0.1:${backendPort}`;
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url !== appOrigin && !url.startsWith(`${appOrigin}/`)) {
      event.preventDefault();
    }
  });
  mainWindow.loadURL(`${appOrigin}/`);
}

// ---- entry points ---------------------------------------------------------

async function startBackend() {
  const bin = backendBin();
  if (!fs.existsSync(bin)) {
    throw Object.assign(new Error(`backend binary not found at ${bin}`),
      { missingBin: bin });
  }
  backendPort = await findFreePort();
  backendChild = spawnBackend(bin, backendPort);
  await waitForBackend(backendPort, HEALTH_TIMEOUT_MS);
}

async function runSmoke() {
  try {
    await startBackend();
    console.log('SMOKE OK');
    await terminateBackend();
    app.exit(0);
  } catch (err) {
    console.error(`SMOKE FAIL: ${(err && err.message) || err}`);
    await terminateBackend();
    app.exit(1);
  }
}

async function runApp() {
  try {
    await startBackend();
  } catch (err) {
    if (err && err.missingBin) {
      showBackendMissingWindow(err.missingBin);
    } else {
      showErrorWindow('SCRIPTY BACKEND FAILED TO START', [
        String((err && err.message) || err),
        'Check the terminal output ([backend] lines) for the underlying error.',
      ]);
    }
    return;
  }
  buildMenu();
  createWindow();
}

app.whenReady().then(() => (SMOKE ? runSmoke() : runApp()));

app.on('activate', () => {
  if (!SMOKE && backendPort !== null && BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('will-quit', (event) => {
  if (tearingDown || !backendAlive()) return;
  event.preventDefault();
  tearingDown = true;
  terminateBackend().then(() => app.quit());
});
