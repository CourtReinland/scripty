'use strict';

// electron-builder afterPack hook.
//
// electron-builder ad-hoc signs the app shell (identity: null) but does NOT
// re-sign files copied in via extraResources. On Apple Silicon any Mach-O with
// an invalid / missing signature is killed by the OS, so we ad-hoc re-sign every
// Mach-O we shipped into Contents/Resources/{backend,ffmpeg}.
//
// We sign bottom-up: nested .dylib/.so first, then the loader executables, so a
// parent's signature covers already-valid children. Ad-hoc signing is
// idempotent (`--force` re-signs in place), so re-running is safe.

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Mach-O magic numbers (little- and big-endian, 32/64-bit, and fat/universal).
const MACHO_MAGICS = new Set([
  0xfeedface, // MH_MAGIC (32, BE-read)
  0xcefaedfe, // MH_CIGAM (32, LE)
  0xfeedfacf, // MH_MAGIC_64
  0xcffaedfe, // MH_CIGAM_64
  0xcafebabe, // FAT_MAGIC (universal)
  0xbebafeca, // FAT_CIGAM
  0xcafebabf, // FAT_MAGIC_64
  0xbfbafeca, // FAT_CIGAM_64
]);

function isMachO(file) {
  let fd;
  try {
    fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(4);
    const n = fs.readSync(fd, buf, 0, 4, 0);
    if (n < 4) return false;
    return MACHO_MAGICS.has(buf.readUInt32BE(0));
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

// Walk a directory, returning regular files (skipping symlinks to avoid double
// work and signing outside the tree), deepest-first so children sign before
// parents.
function walkFilesDepthFirst(root) {
  const out = [];
  function recurse(dir) {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    // Recurse into subdirectories first (depth-first) ...
    for (const ent of entries) {
      if (ent.isSymbolicLink()) continue;
      if (ent.isDirectory()) recurse(path.join(dir, ent.name));
    }
    // ... then collect this directory's own files.
    for (const ent of entries) {
      if (ent.isSymbolicLink()) continue;
      if (ent.isFile()) out.push(path.join(dir, ent.name));
    }
  }
  recurse(root);
  return out;
}

function adhocSign(file) {
  execFileSync('codesign', ['--force', '-s', '-', '--timestamp=none', file],
    { stdio: ['ignore', 'ignore', 'pipe'] });
}

// electron-builder passes an async-capable function the { appOutDir } context.
module.exports = async function afterPackSign(context) {
  const appOutDir = context.appOutDir;
  // Find the .app produced in this out dir.
  let appName = null;
  try {
    appName = fs.readdirSync(appOutDir).find((n) => n.endsWith('.app'));
  } catch (err) {
    console.error(`[afterpack-sign] cannot read appOutDir ${appOutDir}: ${err}`);
    return;
  }
  if (!appName) {
    console.error(`[afterpack-sign] no .app found in ${appOutDir}; skipping`);
    return;
  }
  const resources = path.join(appOutDir, appName, 'Contents', 'Resources');

  // Explicit loader executables that MUST end up signed even if extension-less.
  const backendExe = path.join(
    resources, 'backend', 'scripty-backend', 'scripty-backend');
  const ffmpegExe = path.join(resources, 'ffmpeg', 'bin', 'ffmpeg');
  const ffprobeExe = path.join(resources, 'ffmpeg', 'bin', 'ffprobe');

  const roots = [
    path.join(resources, 'backend'),
    path.join(resources, 'ffmpeg'),
  ];

  let signed = 0;
  let failed = 0;
  const seen = new Set();

  const signOne = (file) => {
    if (seen.has(file)) return;
    seen.add(file);
    try {
      adhocSign(file);
      signed += 1;
    } catch (err) {
      failed += 1;
      const msg = (err && err.stderr && err.stderr.toString()) ||
        (err && err.message) || String(err);
      console.error(`[afterpack-sign] failed to sign ${file}: ${msg.trim()}`);
    }
  };

  // 1) Sign every Mach-O under the bundled trees, deepest-first (dylibs/so before
  //    the executables that load them).
  for (const root of roots) {
    if (!fs.existsSync(root)) {
      console.warn(`[afterpack-sign] resource tree missing (skipped): ${root}`);
      continue;
    }
    for (const file of walkFilesDepthFirst(root)) {
      if (isMachO(file)) signOne(file);
    }
  }

  // 2) Re-sign the loader executables last so their signature seals the tree.
  for (const exe of [ffmpegExe, ffprobeExe, backendExe]) {
    if (fs.existsSync(exe)) signOne(exe);
  }

  console.log(
    `[afterpack-sign] ad-hoc signed ${signed} Mach-O file(s)` +
    (failed ? `, ${failed} failure(s)` : '') +
    ` under ${appName}/Contents/Resources`);
  // Idempotent + non-fatal: never break the build here; the integrator verifies.
};
