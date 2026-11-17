// Record the reference timeline inside the Docker container.

import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require('/opt/runtime/browser/node_modules/playwright');
const {pathToFileURL} = require('node:url');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const fs = require('node:fs');
const c = JSON.parse(fs.readFileSync('/input/render.json', 'utf8'));
(async () => {
  const browser = await chromium.launch({headless: true});
  let ff;
  try {
    const page = await browser.newPage({
      viewport: {width: c.width, height: c.height}, deviceScaleFactor: 1
    });
    page.setDefaultTimeout(30000);
    const warnings = [];
    const warn = message => { warnings.push(message); console.warn(message); };
    page.on('pageerror', e => warn(e.message));
    page.on('requestfailed', r => warn(`${r.url()}: ${(r.failure() || {}).errorText}`));
    await page.goto(pathToFileURL(c.html).href, {waitUntil: 'load'});
    const loadingWarnings = await page.evaluate(async () => {
      await document.fonts.ready;
      const warnings = [];
      await Promise.all([...document.images].map(i => i.decode().catch(e => warnings.push(e.message))));
      return warnings;
    });
    loadingWarnings.forEach(warn);
    const duration = c.duration;
    // Let initial loading overlays/transitions finish before seeking to frame zero.
    await page.waitForTimeout(500);
    const count = Math.ceil(duration * c.fps - 1e-8);
    ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe',
      '-framerate', String(c.fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
      '-threads', '4', '-preset', 'fast', '-crf', '18', '-vf',
      'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-pix_fmt', 'yuv420p',
      '-movflags', '+faststart', c.output], {stdio: ['pipe', 'ignore', 'inherit']});
    const done = new Promise((resolve, reject) => {
      ff.on('error', reject);
      ff.on('close', code => code === 0 ? resolve() : reject(Error(`ffmpeg exited ${code}`)));
      ff.stdin.on('error', reject);
    });
    // Install a handler immediately, including while screenshots are still being captured.
    done.catch(() => {});
    for (let i = 0; i < count; i++) {
      await page.evaluate(async t => {
        await (window.videoCompare || window.reconstruction).seek(t);
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      }, c.html_start + i / c.fps);
      const bytes = await page.screenshot({type: 'png'});
      if (ff.stdin.destroyed) { await done; throw Error('ffmpeg input closed'); }
      if (!ff.stdin.write(bytes)) await once(ff.stdin, 'drain');
      if (i % c.fps === 0) console.log(`Rendering ${i}/${count} frames`);
    }
    ff.stdin.end();
    await done;
    fs.writeFileSync(c.metadata, JSON.stringify({duration, fps: c.fps,
      frame_count: count, width: c.width, height: c.height,
      html_start: c.html_start, warnings}, null, 2));
  } finally {
    if (ff && ff.exitCode === null) ff.kill();
    await browser.close();
  }
})().catch(e => {console.error(e); process.exitCode = 1;});
