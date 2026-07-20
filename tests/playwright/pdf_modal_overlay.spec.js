const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const scriptPath = path.resolve(__dirname, '../../static/pdf-modal.js');
const baseTemplatePath = path.resolve(__dirname, '../../templates/base.html');
const baseStyles = fs.readFileSync(baseTemplatePath, 'utf8').match(/<style>([\s\S]*?)<\/style>/)[1];

function modalHtml(anchorPage = 1) {
  return `
    <button
      id="open-trigger"
      data-pdf-modal-url="/pdf/sample.pdf#page=1"
      data-pdf-modal-path="sample.pdf"
      data-pdf-modal-title="Sample PDF"
      data-pdf-modal-scrapbox-url=""
      data-pdf-modal-pages="${anchorPage}"
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
            <div class="modal-thumb-range-toggle">
              <button id="pdf-modal-thumb-prev" hidden></button>
              <button id="pdf-modal-thumb-next-top" hidden></button>
            </div>
            <div id="pdf-modal-thumb-grid"></div>
            <div class="modal-thumb-range-toggle">
              <button id="pdf-modal-thumb-prev-bottom" hidden></button>
              <button id="pdf-modal-thumb-next" hidden></button>
            </div>
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

async function setupPage(page, { pageCount = 3, detailDelays = {}, anchorPage = 1 } = {}) {
  await page.setContent(modalHtml(anchorPage));
  await page.addStyleTag({ content: baseStyles });
  await page.evaluate(({ pageCount, detailDelays }) => {
    window.__pageCount = pageCount;
    window.__detailDelays = detailDelays;
    window.__thumbnailDelay = 0;
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
        if (window.__thumbnailDelay) {
          await new Promise((resolve) => setTimeout(resolve, window.__thumbnailDelay));
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
  const initialThumbCount = pageCount === null
    ? 3
    : Math.min(pageCount, anchorPage + 10) - Math.max(1, anchorPage - 10) + 1;
  await expect(page.locator('.modal-thumb-zoom-button')).toHaveCount(initialThumbCount);
}

async function clickZoomButton(zoomButton) {
  await zoomButton.locator('..').hover();
  await zoomButton.click();
}

async function expectRangeButtonsDisabled(rangeButtons, disabled) {
  await expect.poll(() => rangeButtons.evaluateAll((buttons) => buttons.every((button) => button.disabled))).toBe(disabled);
}

test('Escape closes only the detail overlay', async ({ page }) => {
  await setupPage(page);
  await clickZoomButton(page.locator('.modal-thumb-zoom-button').first());
  await expect(page.locator('#pdf-modal-thumb-overlay')).toBeVisible();

  await page.keyboard.press('Escape');

  await expect(page.locator('#pdf-modal-thumb-overlay')).toBeHidden();
  await expect(page.locator('#pdf-modal')).toHaveClass(/open/);
});

test('zoom button fetches detail once and cache prevents refetch', async ({ page }) => {
  await setupPage(page);
  const firstZoom = page.locator('.modal-thumb-zoom-button').first();

  await clickZoomButton(firstZoom);
  await expect(page.locator('#pdf-modal-thumb-detail-body img')).toBeVisible();
  await page.locator('#pdf-modal-thumb-detail-close').click();
  await clickZoomButton(firstZoom);

  await expect.poll(() => page.evaluate(() => window.__detailCalls)).toEqual([1]);
});

test('rapid page changes display only the last requested page', async ({ page }) => {
  await setupPage(page, { detailDelays: { 1: 120, 2: 10 } });
  const zoomButtons = page.locator('.modal-thumb-zoom-button');

  await clickZoomButton(zoomButtons.nth(0));
  await clickZoomButton(zoomButtons.nth(1));

  await expect(page.locator('#pdf-modal-thumb-detail-title')).toHaveText('p.2 拡大プレビュー');
  await expect(page.locator('#pdf-modal-thumb-detail-body img')).toHaveAttribute('alt', 'p.2 拡大プレビュー');
});

test('Tab stays inside the detail overlay', async ({ page }) => {
  await setupPage(page);
  await clickZoomButton(page.locator('.modal-thumb-zoom-button').nth(1));
  await expect(page.locator('#pdf-modal-thumb-detail-close')).toBeFocused();

  await page.keyboard.press('Tab');
  await expect(page.locator('#pdf-modal-thumb-detail-prev')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('#pdf-modal-thumb-detail-close')).toBeFocused();
});

test('prev and next are disabled when page count is unknown', async ({ page }) => {
  await setupPage(page, { pageCount: null });
  await clickZoomButton(page.locator('.modal-thumb-zoom-button').first());

  await expect(page.locator('#pdf-modal-thumb-detail-prev')).toBeDisabled();
  await expect(page.locator('#pdf-modal-thumb-detail-next')).toBeDisabled();
});

test('all range controls expand thumbnails and synchronize their disabled state', async ({ page }) => {
  await setupPage(page, { pageCount: 50, anchorPage: 25 });
  const rangeButtons = page.locator(
    '#pdf-modal-thumb-prev, #pdf-modal-thumb-next-top, #pdf-modal-thumb-prev-bottom, #pdf-modal-thumb-next'
  );
  await expect(rangeButtons).toHaveCount(4);
  await expectRangeButtonsDisabled(rangeButtons, false);
  await page.evaluate(() => { window.__thumbnailDelay = 100; });

  await page.locator('#pdf-modal-thumb-next-top').click();
  await expectRangeButtonsDisabled(rangeButtons, true);
  await expect(page.locator('[data-page="45"]')).toBeVisible();
  await expectRangeButtonsDisabled(rangeButtons, false);

  await page.locator('#pdf-modal-thumb-prev-bottom').click();
  await expectRangeButtonsDisabled(rangeButtons, true);
  await expect(page.locator('[data-page="5"]')).toBeVisible();
  await expectRangeButtonsDisabled(rangeButtons, false);

  await page.locator('#pdf-modal-thumb-prev').click();
  await expectRangeButtonsDisabled(rangeButtons, true);
  await expect(page.locator('[data-page="1"]')).toBeVisible();
  await expect(page.locator('#pdf-modal-thumb-prev')).toBeDisabled();
  await expect(page.locator('#pdf-modal-thumb-prev-bottom')).toBeDisabled();

  await page.locator('#pdf-modal-thumb-next').click();
  await expectRangeButtonsDisabled(rangeButtons, true);
  await expect(page.locator('[data-page="50"]')).toBeVisible();
  await expect(page.locator('#pdf-modal-thumb-next-top')).toBeDisabled();
  await expect(page.locator('#pdf-modal-thumb-next')).toBeDisabled();
});

test('zoom button is revealed by hover and keyboard focus on hover-capable devices', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await setupPage(page);
  const zoomButton = page.locator('.modal-thumb-zoom-button').first();

  await expect.poll(() => zoomButton.evaluate((element) => getComputedStyle(element).opacity)).toBe('0');
  await zoomButton.locator('..').hover();
  await expect.poll(() => zoomButton.evaluate((element) => getComputedStyle(element).opacity)).toBe('1');
  await page.locator('#pdf-modal-thumb-heading').focus();
  await zoomButton.focus();
  await expect.poll(() => zoomButton.evaluate((element) => getComputedStyle(element).opacity)).toBe('1');
});

test('zoom button remains available on touch devices', async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, viewport: { width: 375, height: 800 } });
  const page = await context.newPage();
  await setupPage(page);
  const zoomButton = page.locator('.modal-thumb-zoom-button').first();

  await expect.poll(() => zoomButton.evaluate((element) => getComputedStyle(element).opacity)).toBe('1');
  await zoomButton.click();
  await expect(page.locator('#pdf-modal-thumb-overlay')).toBeVisible();
  await context.close();
});
