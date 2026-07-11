const { test, expect } = require('@playwright/test');
const path = require('path');

const scriptPath = path.resolve(__dirname, '../../static/pdf-modal.js');

function modalHtml() {
  return `
    <button
      id="open-trigger"
      data-pdf-modal-url="/pdf/sample.pdf#page=1"
      data-pdf-modal-path="sample.pdf"
      data-pdf-modal-title="Sample PDF"
      data-pdf-modal-scrapbox-url=""
      data-pdf-modal-pages="1"
    >open</button>
    <div class="modal" id="pdf-modal" aria-hidden="true">
      <div class="modal-panel">
        <div id="pdf-modal-title"></div>
        <iframe id="pdf-modal-frame"></iframe>
        <input id="pdf-modal-pages">
        <div id="pdf-modal-chapters-wrap" hidden><div id="pdf-modal-chapters"></div></div>
        <div id="pdf-modal-chapters-summary"></div>
        <a id="pdf-modal-export"></a>
        <a id="pdf-modal-export-md"></a>
        <button id="pdf-modal-add-workspace"></button>
        <div id="pdf-modal-export-status"></div>
        <button id="pdf-modal-fullscreen"></button>
        <a id="pdf-modal-open"></a>
        <div id="pdf-modal-share-menu"></div>
        <details class="modal-share"></details>
        <a id="pdf-modal-scrapbox"></a>
        <button id="pdf-modal-close"></button>
        <div id="pdf-modal-browse-bar"></div>
        <div id="pdf-modal-thumb-bar" hidden></div>
        <button id="pdf-modal-thumb-toggle"></button>
        <button id="pdf-modal-thumb-back"></button>
        <div id="pdf-modal-thumb-heading"></div>
        <div id="pdf-modal-thumb-panel" hidden>
          <div class="modal-thumb-scroll">
            <button id="pdf-modal-thumb-prev" hidden></button>
            <div id="pdf-modal-thumb-grid"></div>
            <button id="pdf-modal-thumb-next" hidden></button>
          </div>
          <div id="pdf-modal-thumb-overlay" hidden>
            <div role="dialog" aria-modal="true" aria-labelledby="pdf-modal-thumb-detail-title">
              <div id="pdf-modal-thumb-detail-title"></div>
              <button id="pdf-modal-thumb-detail-prev">前</button>
              <button id="pdf-modal-thumb-detail-next">次</button>
              <button id="pdf-modal-thumb-detail-close">閉じる</button>
              <div id="pdf-modal-thumb-detail-body" aria-live="polite"></div>
            </div>
          </div>
        </div>
        <button id="pdf-modal-thumb-apply"></button>
        <div id="pdf-modal-thumb-status"></div>
      </div>
    </div>
  `;
}

async function setupPage(page, { pageCount = 3, detailDelays = {} } = {}) {
  await page.setContent(modalHtml());
  await page.evaluate(({ pageCount, detailDelays }) => {
    window.__pageCount = pageCount;
    window.__detailDelays = detailDelays;
    window.__detailCalls = [];
    window.TsundokuPages = {
      validatePageSpec: () => '',
      specToIntervals: (spec) => String(spec || '')
        .split(',')
        .filter(Boolean)
        .map((part) => {
          const [start, end] = part.split('-').map((value) => Number(value));
          return { start, end: end || start, open: false };
        }),
      pagesToSpec: (pages) => pages.join(','),
      mergeSpecs: (specs) => specs.filter(Boolean).join(','),
    };
    window.TsundokuCart = {
      ready: Promise.resolve(),
      load: () => ({ books: {} }),
      save: () => {},
      getActivePack: () => ({ name: 'Pack' }),
      ensureActivePackInteractive: async () => true,
    };
    window.fetch = async (url, options = {}) => {
      const parsed = new URL(url, 'http://example.test');
      if (parsed.pathname === '/pdf-outline') {
        return {
          ok: true,
          json: async () => ({ page_count: window.__pageCount, chapters: [] }),
        };
      }
      if (parsed.pathname === '/pdf-thumbnails') {
        const size = parsed.searchParams.get('size') || 'thumbnail';
        const pages = parsed.searchParams.get('pages');
        if (size === 'detail') {
          const page = Number(pages);
          window.__detailCalls.push(page);
          const delay = window.__detailDelays[String(page)] || 0;
          if (delay) {
            await new Promise((resolve, reject) => {
              const timer = setTimeout(resolve, delay);
              options.signal?.addEventListener('abort', () => {
                clearTimeout(timer);
                reject(new DOMException('Aborted', 'AbortError'));
              });
            });
          }
          return {
            ok: true,
            json: async () => ({ pages: [{ page, data: btoa(`detail-${page}`) }] }),
          };
        }
        const [startText, endText] = pages.split('-');
        const start = Number(startText);
        const end = Number(endText || startText);
        const max = Number.isInteger(window.__pageCount) ? Math.min(end, window.__pageCount) : Math.min(end, 3);
        const payload = [];
        for (let page = start; page <= max; page += 1) {
          payload.push({ page, data: btoa(`thumb-${page}`) });
        }
        return { ok: true, json: async () => ({ pages: payload }) };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    };
  }, { pageCount, detailDelays });
  await page.addScriptTag({ path: scriptPath });
  await page.click('#open-trigger');
  await page.click('#pdf-modal-thumb-toggle');
  await expect(page.locator('.modal-thumb-zoom-button')).toHaveCount(pageCount || 3);
}

test('Escape closes only the detail overlay', async ({ page }) => {
  await setupPage(page);
  await page.locator('.modal-thumb-zoom-button').first().click();
  await expect(page.locator('#pdf-modal-thumb-overlay')).toBeVisible();

  await page.keyboard.press('Escape');

  await expect(page.locator('#pdf-modal-thumb-overlay')).toBeHidden();
  await expect(page.locator('#pdf-modal')).toHaveClass(/open/);
});

test('zoom button fetches detail once and cache prevents refetch', async ({ page }) => {
  await setupPage(page);
  const firstZoom = page.locator('.modal-thumb-zoom-button').first();

  await firstZoom.click();
  await expect(page.locator('#pdf-modal-thumb-detail-body img')).toBeVisible();
  await page.locator('#pdf-modal-thumb-detail-close').click();
  await firstZoom.click();

  await expect.poll(() => page.evaluate(() => window.__detailCalls)).toEqual([1]);
});

test('rapid page changes display only the last requested page', async ({ page }) => {
  await setupPage(page, { detailDelays: { 1: 120, 2: 10 } });
  const zoomButtons = page.locator('.modal-thumb-zoom-button');

  await zoomButtons.nth(0).click();
  await zoomButtons.nth(1).click();

  await expect(page.locator('#pdf-modal-thumb-detail-title')).toHaveText('p.2 拡大プレビュー');
  await expect(page.locator('#pdf-modal-thumb-detail-body img')).toHaveAttribute('alt', 'p.2 拡大プレビュー');
});

test('Tab stays inside the detail overlay', async ({ page }) => {
  await setupPage(page);
  await page.locator('.modal-thumb-zoom-button').nth(1).click();
  await expect(page.locator('#pdf-modal-thumb-detail-close')).toBeFocused();

  await page.keyboard.press('Tab');
  await expect(page.locator('#pdf-modal-thumb-detail-prev')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('#pdf-modal-thumb-detail-close')).toBeFocused();
});

test('prev and next are disabled when page count is unknown', async ({ page }) => {
  await setupPage(page, { pageCount: null });
  await page.locator('.modal-thumb-zoom-button').first().click();

  await expect(page.locator('#pdf-modal-thumb-detail-prev')).toBeDisabled();
  await expect(page.locator('#pdf-modal-thumb-detail-next')).toBeDisabled();
});
