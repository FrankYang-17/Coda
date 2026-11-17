// Run inside the Docker container: node /opt/tools/capture.mjs index.html 5 frame.png
import { chromium } from '/opt/runtime/browser/node_modules/playwright/index.mjs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [file, seconds, output, width = '1280', height = '720'] = process.argv.slice(2);
if (!file || !output || !Number.isFinite(Number(seconds))) {
  console.error('Usage: node capture.mjs <html> <seconds> <png> [width] [height]');
  process.exit(2);
}
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => errors.push(`${request.url()}: ${request.failure()?.errorText}`));
  await page.goto(pathToFileURL(resolve(file)).href, { waitUntil: 'load' });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(image => image.decode()));
  });
  const dimensions = await page.evaluate(() => {
    const api = window.videoCompare || window.reconstruction;
    return api?.width && api?.height ? { width: api.width, height: api.height } : null;
  });
  if (dimensions) await page.setViewportSize(dimensions);
  await page.evaluate(async time => {
    const api = window.videoCompare || window.reconstruction;
    if (!api?.seek) throw new Error('Missing videoCompare.seek(t) or reconstruction.seek(t)');
    await api.seek(time);
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, Number(seconds));
  if (errors.length) throw new Error(errors.join('\n'));
  await page.screenshot({ path: resolve(output) });
  console.log(JSON.stringify({ screenshot: resolve(output), seconds: Number(seconds), viewport: page.viewportSize() }));
} finally {
  await browser.close();
}
