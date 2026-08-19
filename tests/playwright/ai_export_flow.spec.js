const { test, expect } = require('@playwright/test');
const fs = require('fs');

async function createActivePack(page) {
  await page.goto('/workspace');
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
  await page.goto('/search?q=バザール');
  const checkbox = page.locator('.cart-checkbox').first();
  await expect(checkbox).toBeVisible();
  await checkbox.check();
  const saveResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'PUT'
      && /\/api\/packs\/\d+\/items$/.test(new URL(response.url()).pathname)
  ));
  await page.locator('#add-selected-btn').click();
  const saveResponse = await saveResponsePromise;
  expect(saveResponse.ok()).toBe(true);
  await expect(page.locator('#cart-message')).toContainText('1件を資料');
  await page.goto('/workspace');
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

  test('empty pack shows a blocking empty_pack warning and disables the export submit button', async ({ page }) => {
    // R8 PR2 characterization test（設計書§19.2-15 / design.md決定4）:
    // Pythonの warning code 契約（empty_pack はBLOCKING_EXPORT_WARNINGS）
    // と、workspace.html の disabled 判定が一致することをcross-layerで固定する。
    const modal = await openExportModal(page);

    const warningsSection = modal.locator('#ws-export-warnings-section');
    await expect(warningsSection).toBeVisible();
    await expect(modal.locator('#ws-export-warnings')).toContainText('この資料には資料項目がありません');
    await expect(modal.getByRole('button', { name: '書き出す', exact: true })).toBeDisabled();
  });

  test('non-blocking warnings keep the export submit button enabled', async ({ page }) => {
    // R8 PR2 characterization test（設計書§19.2-15 / design.md決定4）:
    // unindexed_pages と plan warning（item_exceeds_limit）は実際にwarningとして
    // 表示されても BLOCKING_EXPORT_WARNINGS に含まれないため、
    // exportSubmitButton を無効化しない。
    //
    // モーダルの既定選択は EXPORT_DESTINATIONS[0]（profile: 'chat'）のため、
    // モーダルを開いた直後の /export/preview リクエストは profile=chat で
    // 発行される。このレスポンス構造は
    // tests/test_web.py の
    // ExportPreviewWarningContractTest.test_item_warnings_precede_plan_warnings_in_profile_payload
    // と同一のitem_stats（未インデックス本 + 巨大本）に対して
    // build_export_preview_payload_for_profile(item_stats, ChatProfile(), pack_name="資料")
    // を実際に呼び出して得た値をそのまま転記したものであり、
    // profile/file_count/archive/chunks を含むchat prof既定の拡張構造・
    // warning文言とも production 実装と一致する。
    await addOneBookToActivePack(page);
    await page.route(/\/api\/packs\/\d+\/export\/preview(?:\?.*)?$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          profile: 'chat',
          estimation: 'approximate',
          estimator: 'char-class-v1',
          book_count: 1,
          item_count: 2,
          total_pages: 3,
          estimated_chars: 90000,
          estimated_tokens: 90000,
          file_count: 2,
          archive: 'zip',
          chunks: [
            {
              filename: '資料_chat_01.md',
              estimated_tokens: 0,
              pages: 2,
              items: [
                {
                  item_id: 1,
                  title: '未インデックス本',
                  pdf_path: 'a.pdf',
                  pages: '1-2',
                  label: null,
                  fragment_index: 1,
                  fragment_count: 1,
                  estimated_tokens: 0,
                },
              ],
            },
            {
              filename: '資料_chat_02.md',
              estimated_tokens: 90000,
              pages: 1,
              items: [
                {
                  item_id: 2,
                  title: '巨大本',
                  pdf_path: 'a.pdf',
                  pages: '1-2',
                  label: null,
                  fragment_index: 1,
                  fragment_count: 1,
                  estimated_tokens: 90000,
                },
              ],
            },
          ],
          warnings: [
            {
              code: 'unindexed_pages',
              item_id: 1,
              message: '「未インデックス本」は未インデックスのため2ページ分を概算に含めていません',
            },
            {
              code: 'item_exceeds_limit',
              item_id: 2,
              message: '「巨大本」は1ファイルの上限を超えるため単独で出力します',
            },
          ],
        }),
      });
    });
    const modal = await openExportModal(page);

    const warningsSection = modal.locator('#ws-export-warnings-section');
    await expect(warningsSection).toBeVisible();
    await expect(modal.locator('#ws-export-warnings')).toContainText('未インデックスのため2ページ分');
    await expect(modal.locator('#ws-export-warnings')).toContainText('上限を超えるため単独で出力します');
    await expect(modal.getByRole('button', { name: '書き出す', exact: true })).toBeEnabled();
  });
});
