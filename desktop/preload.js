// Scripty preload: minimal, typed-ish bridge for the dashboard renderer.
// window.scriptyNative lets the quad UI resolve real filesystem paths for
// dropped File objects and react to menu-driven "Link Known Script…".
'use strict';

const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('scriptyNative', {
  pathForFile: (file) => webUtils.getPathForFile(file),
  onLinkScript: (cb) => ipcRenderer.on('scripty:link-script', (_event, p) => cb(p)),
  platform: process.platform,
});
