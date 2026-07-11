const { test, expect } = require('@playwright/test');

test.describe('Search multiple additions (Phase 3A E2E)', () => {
  test.beforeEach(async ({ page }) => {
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

    // 7. 資料棚に移動し、同じPDFが2つ表示されていることを確認
    await page.goto('http://localhost:8003/workspace');

    const cards = page.locator('.ws-book');
    await expect(cards).toHaveCount(2);

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

    // 9. チェック解除だけでは既存資料項目が削除されないことを検証
    await page.goto('http://localhost:8003/search?q=バザール');
    await checkbox.check();
    await checkbox.uncheck();
    
    // 資料棚に戻り、2件維持されていることを確認
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

    // 資料棚で2件表示されていることを確認
    await page.goto('http://localhost:8003/workspace');
    await expect(page.locator('.ws-book')).toHaveCount(2);
  });
});
