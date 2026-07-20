const { test, expect } = require('@playwright/test');

const EXPORT_OPTIONS = {
  'Markdown分冊': { profile: 'chat', format: 'md' },
  '章単位PDF': { profile: 'chapter', format: 'pdf' },
  'PDF一式': { profile: 'standard', format: 'pdf' },
  'Markdown一式': { profile: 'standard', format: 'md' },
};

async function exportWorkspaceAs(page, optionLabel) {
  const expected = EXPORT_OPTIONS[optionLabel];
  if (!expected) throw new Error(`未知の書き出し方式: ${optionLabel}`);

  await page.getByRole('button', { name: '書き出す', exact: true }).click();
  const modal = page.locator('#ws-export-modal');
  await expect(modal).toHaveClass(/open/);
  await modal.getByRole('radio', { name: optionLabel, exact: false }).check();
  await expect(modal.getByRole('button', { name: '書き出す', exact: true })).toBeEnabled();
  await expect(modal.locator('#ws-export-detail')).not.toContainText('undefined');

  const exportResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/export')
      && url.searchParams.get('profile') === expected.profile
      && url.searchParams.get('format') === expected.format
      && response.status() === 200;
  });
  await modal.getByRole('button', { name: '書き出す', exact: true }).click();
  return exportResponse;
}

test.describe('Search multiple additions (Phase 3A E2E)', () => {
  test.beforeEach(async ({ page }) => {
    page.on('console', msg => {
      console.log(`[Browser Console ${msg.type()}]: ${msg.text()}`);
    });
    page.on('pageerror', err => {
      console.error(`[Browser Page Error]: ${err.stack}`);
    });

    // テスト用にクリーンなパックを作成してアクティブ化
    await page.goto('http://localhost:8003/workspace');
    await page.evaluate(async () => {
      const name = 'Phase3Aテスト資料-' + Math.random().toString(36).substring(7);
      const res = await fetch('/api/packs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      await window.TsundokuCart.activatePack(data.id);
    });
  });

  test('uses workspace header links and management menu', async ({ page }) => {
    await page.goto('http://localhost:8003/workspace');

    await expect(page.getByRole('link', { name: /^資料机/ })).toBeVisible();
    await expect(page.getByRole('link', { name: '本の登録', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '資料机', exact: true })).toBeVisible();
    await expect(page.locator('#ws-pack-list-link')).toBeVisible();
    await expect(page.locator('#ws-add-open')).toBeVisible();
    await expect(page.locator('#ws-search-link')).toBeVisible();
    await expect(page.locator('#ws-pack-delete')).toHaveCount(0);
    await expect(page.locator('details#ws-management')).toHaveCount(1);
    await expect(page.locator('#ws-management > summary')).toHaveText('⋯ 資料管理');

    await page.locator('#ws-pack-list-link').click();
    await expect(page).toHaveURL(/\/packs$/);

    await page.goto('http://localhost:8003/workspace');
    await page.locator('#ws-management > summary').click();
    page.once('dialog', (dialog) => dialog.accept('Phase2Dヘッダー資料'));
    await page.locator('#ws-pack-new').click();
    await expect(page.locator('#ws-pack-select')).toContainText('Phase2Dヘッダー資料');

    page.once('dialog', (dialog) => dialog.accept('Phase2D改名資料'));
    await page.locator('#ws-pack-rename').click();
    await expect(page.locator('#ws-pack-select')).toContainText('Phase2D改名資料');
  });

  test('does not show the removed AI note navigation', async ({ page }) => {
    await page.goto('http://localhost:8003/');

    await expect(page.getByRole('link', { name: 'AIノート', exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: /^資料机/ })).toBeVisible();
    await expect(page.getByRole('link', { name: '本の登録', exact: true })).toBeVisible();
  });

  test('uses one workspace link on the home page', async ({ page }) => {
    await page.goto('http://localhost:8003/');

    const workspaceLink = page.locator('#home-pack-card a.button[href="/workspace"]');
    await expect(workspaceLink).toHaveCount(1);
    await expect(workspaceLink).toHaveText('資料机で整理する');
    await expect(page.getByText('資料棚で編集', { exact: true })).toHaveCount(0);
    await expect(page.getByText('PDF一式を書き出す', { exact: true })).toHaveCount(0);
    await expect(page.getByText('MD一式を書き出す', { exact: true })).toHaveCount(0);

    await workspaceLink.click();
    await expect(page).toHaveURL(/\/workspace$/);
  });

  test('navigates from home through renamed registration and workspace management actions', async ({ page }) => {
    await page.goto('http://localhost:8003/');
    await page.getByRole('link', { name: '本の登録', exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole('heading', { name: 'インデックス実行', exact: true })).toBeVisible();

    await page.goto('http://localhost:8003/');
    await page.locator('#home-pack-card a.button[href="/workspace"]').click();
    await expect(page).toHaveURL(/\/workspace$/);
    await expect(page.getByRole('heading', { name: '資料机', exact: true })).toBeVisible();

    await page.locator('#ws-management > summary').click();
    await expect(page.locator('#ws-management > summary')).toHaveText('⋯ 資料管理');
    await page.locator('#ws-pack-list-link').click();
    await expect(page).toHaveURL(/\/packs$/);
    await expect(page.getByRole('heading', { name: '資料一覧', exact: true })).toBeVisible();
  });

  test('keeps the selected pack control within the viewport on narrow screens', async ({ page }) => {
    const originalViewport = page.viewportSize();
    await page.setViewportSize({ width: 375, height: 812 });
    try {
      await page.evaluate(async () => {
        const name = 'Phase2D狭幅表示を確認するための非常に長い資料名です。横幅を超えずに選択できます';
        const response = await fetch('/api/packs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        });
        const pack = await response.json();
        await window.TsundokuCart.activatePack(pack.id);
      });
      await page.goto('http://localhost:8003/workspace');

      const dimensions = await page.locator('#ws-pack-select').evaluate((select) => {
        const card = select.closest('.ws-controls-card');
        const field = document.getElementById('ws-pack-select-field');
        const label = field?.querySelector('span');
        const rect = (element) => {
          const box = element.getBoundingClientRect();
          return { left: box.left, right: box.right, width: box.width, height: box.height };
        };
        return {
          viewportWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          card: rect(card),
          field: rect(field),
          select: rect(select),
          label: rect(label),
        };
      });

      expect(dimensions.select.right).toBeLessThanOrEqual(dimensions.card.right + 0.5);
      expect(dimensions.select.right).toBeLessThanOrEqual(dimensions.viewportWidth + 0.5);
      expect(dimensions.field.right).toBeLessThanOrEqual(dimensions.card.right + 0.5);
      expect(dimensions.label.height).toBeLessThanOrEqual(dimensions.label.width);
      expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewportWidth);
    } finally {
      if (originalViewport) {
        await page.setViewportSize(originalViewport);
      }
    }
  });

  test('adds the same PDF multiple times from search results and verifies independent identities', async ({ page }) => {
    // 1. 検索画面へ遷移（確実にヒットする "バザール" で検索）
    await page.goto('http://localhost:8003/search?q=バザール');

    const checkbox = page.locator('.cart-checkbox').first();
    await expect(checkbox).toBeVisible();

    const pdfTitle = await checkbox.getAttribute('data-cart-title');

    // 2. チェックボックスをONにする
    await checkbox.check();

    // 3. 「選択した本を追加」ボタンが有効化され、件数が表示されていることを確認
    const addBtn = page.locator('#add-selected-btn');
    await expect(addBtn).toBeEnabled();
    await expect(addBtn).toHaveText('選択した本を追加（1件）');

    // 4. 追加ボタンをクリック
    await addBtn.click();

    // 5. 成功メッセージが表示され、チェックボックスがクリアされることを確認
    const msg = page.locator('#cart-message');
    await expect(msg).toContainText('1件を資料');
    await expect(checkbox).not.toBeChecked();
    await expect(addBtn).toBeDisabled();

    // 6. もう一度同じPDFをチェックして追加する（2回目）
    await checkbox.check();
    await expect(addBtn).toBeEnabled();
    await addBtn.click();
    await expect(msg).toContainText('1件を資料');
    await expect(checkbox).not.toBeChecked();

    // 7. 資料机に移動し、同じPDFが2つ表示されていることを確認
    await page.goto('http://localhost:8003/workspace');

    const cards = page.locator('.ws-book');
    await expect(cards).toHaveCount(2);
    await expect(page.locator('#nav-workspace-count')).toHaveText('1冊');
    await expect(page.locator('#ws-pack-select')).toContainText('（1冊）');

    const title1 = await cards.nth(0).locator('.ws-book-title').textContent();
    const title2 = await cards.nth(1).locator('.ws-book-title').textContent();
    expect(title1).toContain(pdfTitle);
    expect(title2).toContain(pdfTitle);

    // 8. 2つのカードの dataset.itemId / clientId が異なることを確認
    const itemId1 = await cards.nth(0).getAttribute('data-item-id');
    const itemId2 = await cards.nth(1).getAttribute('data-item-id');
    expect(itemId1).not.toBeNull();
    expect(itemId2).not.toBeNull();
    expect(itemId1).not.toEqual(itemId2);

    const clientId1 = await cards.nth(0).getAttribute('data-client-id');
    const clientId2 = await cards.nth(1).getAttribute('data-client-id');
    if (clientId1 && clientId2) {
      expect(clientId1).not.toEqual(clientId2);
    }

    await page.goto('http://localhost:8003/');
    await expect(page.locator('#home-pack-summary')).toContainText('1冊');

    // 9. チェック解除だけでは既存資料項目が削除されないことを検証
    await page.goto('http://localhost:8003/search?q=バザール');
    await expect(page.locator('#cart-pack-select')).toContainText('（1冊）');
    await checkbox.check();
    await checkbox.uncheck();
    
    // 資料机に戻り、2件維持されていることを確認
    await page.goto('http://localhost:8003/workspace');
    await expect(page.locator('.ws-book')).toHaveCount(2);
  });

  test('adds multiple different PDFs at once', async ({ page }) => {
    // 確実に複数ヒットする "Core" で検索
    await page.goto('http://localhost:8003/search?q=Core');

    const checkboxes = page.locator('.cart-checkbox');
    const count = await checkboxes.count();
    expect(count).toBeGreaterThanOrEqual(2);

    // 複数チェック
    await checkboxes.nth(0).check();
    await checkboxes.nth(1).check();

    const addBtn = page.locator('#add-selected-btn');
    await expect(addBtn).toHaveText('選択した本を追加（2件）');
    await addBtn.click();

    await expect(page.locator('#cart-message')).toContainText('2件を資料');

    // 資料机で2件表示されていることを確認
    await page.goto('http://localhost:8003/workspace');
    await expect(page.locator('.ws-book')).toHaveCount(2);
    await expect(page.locator('#nav-workspace-count')).toHaveText('2冊');
    await expect(page.locator('#ws-pack-select')).toContainText('（2冊）');
  });

  test('adds the same PDF multiple times from PDF preview modal and verifies independent identities and edits', async ({ page }, testInfo) => {
    // 1. 検索画面へ遷移し、モーダルを開くボタンを探す
    await page.goto('http://localhost:8003/search?q=バザール');

    const previewBtn = page.locator('[data-pdf-modal-url]').first();
    await expect(previewBtn).toBeVisible();

    const pdfTitle = await previewBtn.getAttribute('data-pdf-modal-title');

    // 2. モーダルを開く
    await previewBtn.click();
    const modal = page.locator('#pdf-modal');
    await expect(modal).toHaveClass(/open/);

    // 3. モーダル内で、1回目の追加（ページ範囲 "1-5"）
    const pagesInput = page.locator('#pdf-modal-pages');
    await pagesInput.fill('1-5');
    
    // 追加ボタンをクリック
    const addBtn = page.locator('#pdf-modal-add-workspace');
    await expect(addBtn).toHaveText(/に追加$/); // 「資料名」に追加
    await addBtn.click();

    // 成功メッセージの確認
    const status = page.locator('#pdf-modal-export-status');
    await expect(status).toContainText('追加しました');

    // 4. そのまま、2回目の追加（ページ範囲 "10-15"）
    await pagesInput.fill('10-15');
    await addBtn.click();
    await expect(status).toContainText('追加しました');

    // モーダルを閉じる
    await page.locator('#pdf-modal-close').click();
    await expect(modal).not.toHaveClass(/open/);

    // 5. 資料机に移動し、同じPDFが2つ表示されていることを確認
    await page.goto('http://localhost:8003/workspace');

    const cards = page.locator('.ws-book');
    await expect(cards).toHaveCount(2);

    // 6. それぞれ異なるページ範囲と異なるID（id, clientId）を持っていることを検証
    const pagesPill1 = await cards.nth(0).locator('.ws-book-range').textContent();
    const pagesPill2 = await cards.nth(1).locator('.ws-book-range').textContent();
    expect(pagesPill1).toContain('5ページ'); // 1-5 = 5ページ
    expect(pagesPill2).toContain('6ページ'); // 10-15 = 6ページ

    const itemId1 = await cards.nth(0).getAttribute('data-item-id');
    const itemId2 = await cards.nth(1).getAttribute('data-item-id');
    expect(itemId1).not.toBeNull();
    expect(itemId2).not.toBeNull();
    expect(itemId1).not.toEqual(itemId2);

    // 7. 片方（項目A）を編集して、他方（項目B）が影響を受けないことを検証
    // 項目A（1枚目のカード）の「PDFで選ぶ」ボタンをクリックしてモーダルを開く
    const editBtn1 = cards.nth(0).locator('button:has-text("PDFで選ぶ")');
    await editBtn1.click();
    await expect(modal).toHaveClass(/open/);

    // ボタンテキストが「ページ範囲を更新」になっていることを確認
    await expect(addBtn).toHaveText('ページ範囲を更新');
    await expect(pagesInput).toHaveValue('1-5');

    // ページ範囲を "2-4" に変更して「ページ範囲を更新」をクリック
    await pagesInput.fill('2-4');
    await addBtn.click();
    await expect(status).toContainText('更新しました');

    // モーダルを閉じる
    await page.locator('#pdf-modal-close').click();

    // サーバーへの保存が完了するのを待つ
    await page.evaluate(async () => {
      await window.TsundokuCart.flushPendingSave();
    });

    // 資料机で、1枚目のカードの表示範囲のみが更新され、2枚目のカードが変更されていないことを確認
    // （かつ、編集操作によってカードの件数が増えていないことも確認）
    await expect(cards).toHaveCount(2);
    const pagesPill1Updated = await cards.nth(0).locator('.ws-book-range').textContent();
    const pagesPill2Updated = await cards.nth(1).locator('.ws-book-range').textContent();
    expect(pagesPill1Updated).toContain('3ページ'); // 2-4 = 3ページ
    expect(pagesPill2Updated).toContain('6ページ'); // 10-15 = 6ページ (変更なし)

    // 8. ページ再読み込み後も維持される
    await page.reload();
    await expect(cards).toHaveCount(2);
    expect(await cards.nth(0).locator('.ws-book-range').textContent()).toContain('3ページ');
    expect(await cards.nth(1).locator('.ws-book-range').textContent()).toContain('6ページ');

    // 8.5 資料データを書き出す (JSONエクスポート)
    const jsonExportResponse = page.waitForResponse((response) => (
      response.url().includes('/export?format=json') && response.status() === 200
    ));
    await page.locator('#ws-management > summary').click();
    await page.locator('#ws-export-json').click();
    const fs = require('fs');
    const path = require('path');
    const jsonBytes = await (await jsonExportResponse).body();
    const jsonPayload = JSON.parse(jsonBytes.toString('utf-8'));
    const jsonExportPath = testInfo.outputPath('workspace-export.json');
    fs.writeFileSync(jsonExportPath, jsonBytes);
    
    expect(jsonPayload.version).toBe(3);
    expect(jsonPayload.items).toHaveLength(2);
    expect(jsonPayload.items[0].pdf_path).toContain('cathedral.pdf');
    expect(jsonPayload.items[0].title).toContain('1 伽藍方式とバザール方式');
    expect(jsonPayload.items[0].pages).toBe('2-4');
    expect(jsonPayload.items[0].collapsed).toBe(false);
    expect(jsonPayload.items[0].position).toBe(0);
    expect(jsonPayload.items[1].pdf_path).toContain('cathedral.pdf');
    expect(jsonPayload.items[1].title).toContain('1 伽藍方式とバザール方式');
    expect(jsonPayload.items[1].pages).toBe('10-15');
    expect(jsonPayload.items[1].collapsed).toBe(false);
    expect(jsonPayload.items[1].position).toBe(1);
    await page.locator('#ws-management > summary').click();

    // 9. PDF一式を書き出す
    const pdfExportResponse = await exportWorkspaceAs(page, 'PDF一式');
    const downloadPath = testInfo.outputPath('workspace-export.zip');
    fs.writeFileSync(downloadPath, await pdfExportResponse.body());

    // Node.js の child_process を使って ZIP 内のファイル名と各PDFのページ数を検証する
    const { execSync } = require('child_process');

    // unzip -l でファイル名一覧を取得
    const fileList = execSync(`unzip -l "${downloadPath}"`).toString();
    console.log("ZIP FILE LIST:\n", fileList);

    // 期待するファイルが含まれていることを確認
    // 連番_書籍タイトル_pページ範囲.pdf
    expect(fileList).toContain('manifest.md');
    expect(fileList).toContain('01_1_伽藍方式とバザール方式_p.1_p.2_p.3_p.10_15件_p2-4.pdf');
    expect(fileList).toContain('02_1_伽藍方式とバザール方式_p.1_p.2_p.3_p.10_15件_p10-15.pdf');

    // 実際に解凍して PDF のページ数を検証する
    const extractDir = path.join(path.dirname(downloadPath), 'extracted_zip');
    if (fs.existsSync(extractDir)) {
      fs.rmSync(extractDir, { recursive: true });
    }
    fs.mkdirSync(extractDir);
    execSync(`unzip "${downloadPath}" -d "${extractDir}"`);

    // 01_...pdf = 2-4 (3ページ)
    const pdfPath1 = path.join(extractDir, '01_1_伽藍方式とバザール方式_p.1_p.2_p.3_p.10_15件_p2-4.pdf');
    const pdfBuffer1 = fs.readFileSync(pdfPath1);
    const pdfText1 = pdfBuffer1.toString('binary');
    const pageCount1 = (pdfText1.match(/\/Type\s*\/Page\b/g) || []).length;
    expect(pageCount1).toBe(3);

    // 02_...pdf = 10-15 (6ページ)
    const pdfPath2 = path.join(extractDir, '02_1_伽藍方式とバザール方式_p.1_p.2_p.3_p.10_15件_p10-15.pdf');
    const pdfBuffer2 = fs.readFileSync(pdfPath2);
    const pdfText2 = pdfBuffer2.toString('binary');
    const pageCount2 = (pdfText2.match(/\/Type\s*\/Page\b/g) || []).length;
    expect(pageCount2).toBe(6);

    // 各書き出し方式は、選択された profile / format をそのままHTTPクエリへ渡す。
    for (const optionLabel of ['Markdown分冊', '章単位PDF', 'Markdown一式']) {
      const response = await exportWorkspaceAs(page, optionLabel);
      expect(response.headers()['content-type']).toContain('application/zip');
    }

    // 9.5. 資料データを読み込む (JSONインポートのUIテスト)
    const importFileInputLocator = page.locator('#ws-import-file-input');
    const importResponse = page.waitForResponse((response) => (
      response.url().endsWith('/api/packs/import') && response.status() === 201
    ));
    await importFileInputLocator.setInputFiles(jsonExportPath);
    const importedPack = await (await importResponse).json();
    await expect(page.locator('#ws-pack-select')).toHaveValue(String(importedPack.id));

    // インポート完了後の新しいワークスペースで、2つのカードが正しく復元されていることを確認
    const importedCards = page.locator('.ws-book');
    await expect(importedCards).toHaveCount(2);
    await expect(importedCards.nth(0)).toContainText('3ページ');
    await expect(importedCards.nth(1)).toContainText('6ページ');

    const cards2 = importedCards;

    // 片方を削除した場合、残った項目だけが出力される
    // 1件目のカード（2-4 ページ）を削除
    await cards2.nth(0).locator('button:has-text("削除")').click();
    await page.evaluate(async () => {
      await window.TsundokuCart.flushPendingSave();
    });
    await expect(cards2).toHaveCount(1);

    // 再度エクスポート
    const pdfExportResponse2 = await exportWorkspaceAs(page, 'PDF一式');
    const downloadPath2 = testInfo.outputPath('workspace-export-after-delete.zip');
    fs.writeFileSync(downloadPath2, await pdfExportResponse2.body());

    const fileList2 = execSync(`unzip -l "${downloadPath2}"`).toString();
    expect(fileList2).toContain('manifest.md');
    expect(fileList2).not.toContain('01_1_伽藍方式とバザール方式_p.1_p.2_p.3_p.10_15件_p2-4.pdf');
    expect(fileList2).toContain('01_1_伽藍方式とバザール方式_p.1_p.2_p.3_p.10_15件_p10-15.pdf'); // 残ったのが01番になる
  });
});
