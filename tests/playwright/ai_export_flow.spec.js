const { test, expect } = require('@playwright/test');
const fs = require('fs');

async function createActivePack(page) {
  await page.goto('http://localhost:8003/workspace');
  await page.evaluate(async () => {
    const name = `Phase3E書き出し資料-${Math.random().toString(36).slice(2)}`;
    const response = await fetch('/api/packs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const pack = await response.json();
    await window.TsundokuCart.activatePack(pack.id);
  });
}

async function addOneBookToActivePack(page) {
  await page.goto('http://localhost:8003/search?q=バザール');
  const checkbox = page.locator('.cart-checkbox').first();
  await expect(checkbox).toBeVisible();
  await checkbox.check();
  await page.locator('#add-selected-btn').click();
  await expect(page.locator('#cart-message')).toContainText('1件を資料');
  await page.goto('http://localhost:8003/workspace');
  await expect(page.locator('.ws-book')).toHaveCount(1);
}

async function openExportModal(page) {
  await page.getByRole('button', { name: '書き出す', exact: true }).click();
  const modal = page.locator('#ws-export-modal');
  await expect(modal).toHaveClass(/open/);
  return modal;
}

test.describe('AI export flow (Phase 3E E2E)', () => {
  test.beforeEach(async ({ page }) => {
    page.on('console', (message) => {
      console.log(`[Browser Console ${message.type()}]: ${message.text()}`);
    });
    await createActivePack(page);
  });

  test('export destination descriptions mention target AI services', async ({ page }) => {
    const modal = await openExportModal(page);

    await expect(modal.getByRole('radio', { name: /PDF一式/ }).locator('xpath=..')).toContainText('Gemini');
    await expect(modal.getByRole('radio', { name: /Markdown一式/ }).locator('xpath=..')).toContainText('Gemini');
    const chapterOption = modal.getByRole('radio', { name: /章単位PDF/ }).locator('xpath=..');
    await expect(chapterOption).toContainText('NotebookLM');
    await expect(chapterOption).toContainText('ZIPを解凍');
    await expect(chapterOption).toContainText('ZIPファイル自体はアップロードできません');
  });

  test('token estimate is consistently marked as approximate in the export modal', async ({ page }) => {
    await addOneBookToActivePack(page);
    const modal = await openExportModal(page);
    const detail = modal.locator('#ws-export-detail');

    await expect(detail).toContainText('推定トークン数: 約');
    await expect(detail).not.toContainText('undefined');
    await expect(detail).not.toContainText('NaN');
    await expect(detail).not.toContainText('null');
  });

  test('completes an export and downloads a non-empty zip file', async ({ page }, testInfo) => {
    await addOneBookToActivePack(page);
    const modal = await openExportModal(page);
    await modal.getByRole('radio', { name: /PDF一式/ }).check();
    await expect(modal.locator('#ws-export-detail')).toContainText('推定トークン数: 約');
    await expect(modal.getByRole('button', { name: '書き出す', exact: true })).toBeEnabled();

    const downloadPromise = page.waitForEvent('download');
    await modal.getByRole('button', { name: '書き出す', exact: true }).click();
    const download = await downloadPromise;
    const downloadPath = testInfo.outputPath('phase3e-export.zip');
    await download.saveAs(downloadPath);

    expect(download.suggestedFilename()).toMatch(/\.zip$/i);
    expect(fs.existsSync(downloadPath)).toBe(true);
    expect(fs.statSync(downloadPath).size).toBeGreaterThan(0);
    expect(await download.failure()).toBeNull();
  });
});
