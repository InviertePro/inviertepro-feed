const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage({ viewport: { width: 900, height: 1300 }, deviceScaleFactor: 1.4 });
  await p.goto('file:///home/claude/guia-instagram-api.html');
  await p.waitForTimeout(900);
  await p.screenshot({ path: 'guia-top.png' });
  await b.close();
})();
