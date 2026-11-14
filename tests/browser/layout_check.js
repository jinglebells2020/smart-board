// Opens the page at several window sizes and prints one JSON line per
// size: the drawing's height, how many lines of Zip's words show, and
// anything that overlaps. Used by tests/test_page_layout.py.
// Usage: node tests/browser/layout_check.js http://127.0.0.1:PORT 1280x585 ...
const { chromium } = require('playwright');

(async () => {
  const [base, ...sizes] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(base);
  await page.waitForTimeout(800);
  await page.click('#check');               // a mistake: hint, red lights, "Learn why" tab
  await page.waitForFunction(() => !document.getElementById('mini-open').hidden, null, { timeout: 30000 });
  for (const size of sizes) {
    const [w, h] = size.split('x').map(Number);
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(250);
    const m = await page.evaluate(() => {
      const box = (el) => { const b = el.getBoundingClientRect(); return { t: b.top, b: b.bottom, h: b.height }; };
      const hint = document.getElementById('hint');
      const lines = (hint.clientHeight - 32) / parseFloat(getComputedStyle(hint).lineHeight);
      const overlaps = [];
      for (const column of ['.stage', '.zip']) {
        const zone = document.querySelector(column);
        const parts = [...zone.querySelectorAll(column === '.stage'
            ? ':scope > .stage-head, :scope > .drawing, :scope > .legend, :scope > .device'
            : '.zip-top, #mini, #bubble, #offline-note, #mini-open, #ask-power, .actions')]
          .filter((e) => e.offsetParent !== null).map((e) => ({ n: e.id || e.className, ...box(e) }))
          .sort((a, b) => a.t - b.t);
        for (let i = 1; i < parts.length; i++) {
          if (parts[i].t < parts[i - 1].b - 0.5) overlaps.push(parts[i - 1].n + ' / ' + parts[i].n);
        }
        const z = box(zone);
        for (const p of parts) if (p.b > z.b + 0.5 || p.t < z.t - 0.5) overlaps.push(p.n + ' outside ' + column);
      }
      return { drawing: Math.round(box(document.getElementById('bb')).h), lines: +lines.toFixed(2),
               overlaps, sideways: document.documentElement.scrollWidth > innerWidth };
    });
    console.log(JSON.stringify({ size, ...m }));
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
