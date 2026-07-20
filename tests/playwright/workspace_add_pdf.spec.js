const { test, expect } = require('@playwright/test');

function createOutlineFreePdf(pageCount = 5) {
  const objects = [];
  const pageObjectNumbers = [];
  const streamObjectNumbers = [];
  const fontObjectNumber = 3;

  for (let page = 1; page <= pageCount; page += 1) {
    pageObjectNumbers.push(4 + (page - 1) * 2);
    streamObjectNumbers.push(5 + (page - 1) * 2);
  }

  objects[1] = '<< /Type /Catalog /Pages 2 0 R >>';
  objects[2] = `<< /Type /Pages /Kids [${pageObjectNumbers.map((number) => `${number} 0 R`).join(' ')}] /Count ${pageCount} >>`;
  objects[fontObjectNumber] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>';

  for (let page = 1; page <= pageCount; page += 1) {
    const pageObjectNumber = pageObjectNumbers[page - 1];
    const streamObjectNumber = streamObjectNumbers[page - 1];
    const content = `BT /F1 32 Tf 72 720 Td (Outline-free page ${page}) Tj ET`;
    objects[pageObjectNumber] = [
      '<< /Type /Page',
      '/Parent 2 0 R',
      '/MediaBox [0 0 612 792]',
      `/Resources << /Font << /F1 ${fontObjectNumber} 0 R >> >>`,
      `/Contents ${streamObjectNumber} 0 R`,
      '>>',
    ].join(' ');
    objects[streamObjectNumber] = `<< /Length ${Buffer.byteLength(content, 'ascii')} >>\nstream\n${content}\nendstream`;
  }

  let pdf = '%PDF-1.4\n%\xE2\xE3\xCF\xD3\n';
  const offsets = [0];
  for (let objectNumber = 1; objectNumber < objects.length; objectNumber += 1) {
    offsets[objectNumber] = Buffer.byteLength(pdf, 'binary');
    pdf += `${objectNumber} 0 obj\n${objects[objectNumber]}\nendobj\n`;
  }
  const xrefOffset = Buffer.byteLength(pdf, 'binary');
  pdf += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let objectNumber = 1; objectNumber < objects.length; objectNumber += 1) {
    pdf += `${String(offsets[objectNumber]).padStart(10, '0')} 00000 n \n`;
  }
  pdf += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(pdf, 'binary');
}

async function createOutlineFreeWorkspaceItem(page) {
  const filename = `outline-free-${Date.now()}-${Math.random().toString(36).slice(2)}.pdf`;
  const upload = await page.request.post(
    `http://localhost:8003/settings/pdf-upload?${new URLSearchParams({ filename })}`,
    {
      headers: { 'Content-Type': 'application/pdf' },
      data: createOutlineFreePdf(),
    },
  );
  expect(upload.status()).toBe(201);
  const pdfPath = await upload.text();

  const outline = await page.request.get(
    `http://localhost:8003/pdf-outline?${new URLSearchParams({ pdf_path: pdfPath })}`,
  );
  expect(outline.status()).toBe(200);
  await expect(outline.json()).resolves.toEqual({ page_count: 5, chapters: [] });

  await createActivePack(page);
  await page.evaluate(async ({ pdfPath, filename }) => {
    await window.TsundokuCart.ready;
    const cart = window.TsundokuCart.load();
    const item = {
      clientId: window.TsundokuCart.createClientId(),
      pdf_path: pdfPath,
      title: `アウトラインなしPDF ${filename}`,
      pages: '',
      collapsed: false,
    };
    cart.items = [item];
    window.TsundokuCart.save(cart);
    await window.TsundokuCart.flushPendingSave();
  }, { pdfPath, filename });

  await page.goto('http://localhost:8003/workspace');
  await page.evaluate(() => window.TsundokuCart.ready);
  await expect(page.locator('.ws-book')).toHaveCount(1);
}

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

test('selects visible thumbnails from an outline-free PDF and keeps the range after reload', async ({ page }) => {
  await createOutlineFreeWorkspaceItem(page);

  await expect(page.locator('.ws-chapters-holder')).toBeEmpty();
  await page.getByRole('button', { name: 'PDFで選ぶ', exact: true }).click();
  await expect(page.locator('#pdf-modal')).toHaveClass(/open/);
  await expect(page.locator('#pdf-modal-chapters-wrap')).toBeHidden();

  await page.locator('#pdf-modal-thumb-toggle').click();
  await expect(page.locator('#pdf-modal-thumb-grid .modal-thumb-item')).toHaveCount(5);
  await expect(page.locator('#pdf-modal-thumb-grid .modal-thumb-item img')).toHaveCount(5);
  await expect(page.locator('#pdf-modal-thumb-check-3').locator('xpath=../..')).toContainText('p.3');
  await page.locator('#pdf-modal-thumb-check-3').check();
  await expect(page.locator('#pdf-modal-thumb-apply')).toHaveText('1ページを資料に反映');
  await page.locator('#pdf-modal-thumb-apply').click();
  await expect(page.locator('#pdf-modal-thumb-status')).toContainText('1ページを資料に反映しました');
  await page.evaluate(() => window.TsundokuCart.flushPendingSave());

  await page.reload();
  await page.evaluate(() => window.TsundokuCart.ready);
  await expect(page.locator('.ws-pages-input')).toHaveValue('3');
});

test('saves direct page input for an outline-free PDF and keeps it after reload', async ({ page }) => {
  await createOutlineFreeWorkspaceItem(page);

  await expect(page.locator('.ws-chapters-holder')).toBeEmpty();
  const pagesInput = page.locator('.ws-pages-input');
  await pagesInput.fill('2-3,5');
  await expect(page.locator('.ws-error')).toBeHidden();
  await page.evaluate(() => window.TsundokuCart.flushPendingSave());

  await page.reload();
  await page.evaluate(() => window.TsundokuCart.ready);
  await expect(page.locator('.ws-pages-input')).toHaveValue('2-3,5');
});
