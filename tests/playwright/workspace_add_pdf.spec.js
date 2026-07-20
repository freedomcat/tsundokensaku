const { test, expect } = require('@playwright/test');

async function createActivePack(page) {
  await page.goto('http://localhost:8003/workspace');
  await page.evaluate(async () => {
    const response = await fetch('/api/packs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: `Phase2 PDF導線-${Math.random().toString(36).slice(2)}` }),
    });
    const pack = await response.json();
    await window.TsundokuCart.activatePack(pack.id);
  });
}

async function addBookToActivePack(page) {
  await page.goto('http://localhost:8003/search?q=バザール');
  const checkbox = page.locator('.cart-checkbox').first();
  await expect(checkbox).toBeVisible();
  const title = await checkbox.getAttribute('data-cart-title');
  expect(title).not.toBeNull();
  await checkbox.check();
  await page.locator('#add-selected-btn').click();
  await expect(page.locator('#cart-message')).toContainText('1件を資料');
  await page.evaluate(() => window.TsundokuCart.flushPendingSave());
  return page.evaluate(() => {
    const item = window.TsundokuCart.load().items[0];
    return {
      itemKey: window.TsundokuCart.itemKey(item),
      pdfPath: item.pdf_path,
      title: item.title,
    };
  });
}

test('opens the selected book PDF from the workspace page-add modal and updates its pages', async ({ page }) => {
  await createActivePack(page);
  const { itemKey, title } = await addBookToActivePack(page);
  await page.goto('http://localhost:8003/workspace');
  await page.evaluate(() => window.TsundokuCart.ready);

  await page.route('**/search-pages**', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ indexed: true, pages: [{ page_number: 1, snippet: '本文検索の結果' }] }),
    });
  });
  await page.route('**/pdf-outline**', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ page_count: 3, chapters: [] }) });
  });

  await page.locator('#ws-add-open').click();
  await expect(page.locator('#ws-add-title')).toHaveText('追加するページを選ぶ');
  await expect(page.locator('#ws-add-open-pdf')).toBeVisible();
  await expect(page.locator('#ws-add-open-pdf')).toBeDisabled();

  await page.locator('#ws-add-scope').selectOption(itemKey);
  await expect(page.locator('#ws-add-open-pdf')).toBeEnabled();
  await page.locator('#ws-add-query').fill('本文');
  await page.locator('#ws-add-form').evaluate((form) => form.requestSubmit());
  await expect(page.locator('#ws-add-results')).toContainText('本文検索の結果');

  await page.locator('#ws-add-open-pdf').click();
  await expect(page.locator('#ws-add-modal')).not.toHaveClass(/open/);
  await expect(page.locator('#pdf-modal')).toHaveClass(/open/);
  await expect(page.locator('#pdf-modal-title')).toHaveText(title);
  await expect(page.locator('#pdf-modal-chapters-wrap')).toBeHidden();

  await page.locator('#pdf-modal-pages').fill('1');
  await page.locator('#pdf-modal-add-workspace').click();
  await expect(page.locator('#pdf-modal-export-status')).toContainText('ページ範囲を');

  await page.locator('#pdf-modal-close').click();
  await expect(page.locator('#pdf-modal')).not.toHaveClass(/open/);
  await expect(page.locator('.ws-pages-input').first()).toHaveValue('1');
});

test('keeps duplicate PDF entries distinct when opening the page-add PDF preview', async ({ page }) => {
  await createActivePack(page);
  await addBookToActivePack(page);
  await page.goto('http://localhost:8003/workspace');
  await page.evaluate(() => window.TsundokuCart.ready);

  const entries = await page.evaluate(() => {
    const cart = window.TsundokuCart.load();
    const first = cart.items[0];
    first.pages = '10-20';
    const second = {
      clientId: window.TsundokuCart.createClientId(),
      pdf_path: first.pdf_path,
      title: first.title,
      pages: '80-95',
      collapsed: false,
    };
    cart.items = [first, second];
    window.TsundokuCart.save(cart);
    document.dispatchEvent(new Event('tsundoku-cart-updated'));
    return {
      firstKey: window.TsundokuCart.itemKey(first),
      secondKey: window.TsundokuCart.itemKey(second),
      title: first.title,
    };
  });

  await page.route('**/pdf-outline**', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ page_count: 120, chapters: [] }) });
  });

  await expect(page.locator('.ws-book')).toHaveCount(2);
  await page.locator('#ws-add-open').click();
  const options = page.locator('#ws-add-scope option');
  await expect(options).toHaveCount(3);
  await expect(options.nth(1)).toHaveText(`${entries.title}（p.10-20）`);
  await expect(options.nth(2)).toHaveText(`${entries.title}（p.80-95）`);

  await page.locator('#ws-add-scope').selectOption(entries.secondKey);
  await page.locator('#ws-add-open-pdf').click();
  await expect(page.locator('#pdf-modal')).toHaveClass(/open/);
  await expect(page.locator('#pdf-modal-pages')).toHaveValue('80-95');

  await page.locator('#pdf-modal-pages').fill('81-96');
  await page.locator('#pdf-modal-add-workspace').click();
  await expect.poll(() => page.evaluate(({ firstKey, secondKey }) => {
    const items = window.TsundokuCart.load().items;
    return [
      items.find((item) => window.TsundokuCart.itemKey(item) === firstKey)?.pages,
      items.find((item) => window.TsundokuCart.itemKey(item) === secondKey)?.pages,
    ];
  }, entries)).toEqual(['10-20', '81-96']);
});
