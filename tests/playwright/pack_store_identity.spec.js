const { test, expect } = require('@playwright/test');

// ストアの初期化とAPIモックのヘルパー
async function setupStorePage(page, { activePackId = 4, initialItems = [], mockPutStatus = 200, mockPutDelay = 0 } = {}) {
  page.on('console', msg => {
    console.log(`[Browser Console ${msg.type()}]: ${msg.text()}`);
  });
  page.on('pageerror', err => {
    console.error(`[Browser Page Error]: ${err.stack}`);
  });

  // モック API の設定 (gotoの前に設定する必要がある)
  let putCallCount = 0;
  let lastPutPayload = null;

  await page.route('**/api/packs', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        packs: activePackId ? [{ id: activePackId, name: 'テスト資料' }] : [],
        active_pack_id: activePackId,
      }),
    });
  });

  await page.route(`**/api/packs/${activePackId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: activePackId,
        name: 'テスト資料',
        version: 3,
        items: initialItems,
      }),
    });
  });

  await page.route(`**/api/packs/${activePackId}/items`, async (route) => {
    putCallCount++;
    const request = route.request();
    const payload = JSON.parse(request.postData() || '{}');
    lastPutPayload = payload;

    const responseItems = (payload.items || []).map((item, index) => {
      return {
        ...item,
        id: item.id || (100 + index), // 確定IDを割り当てる
        position: index,
        updatedAt: new Date().toISOString(),
      };
    });

    if (mockPutDelay > 0) {
      await new Promise(resolve => setTimeout(resolve, mockPutDelay));
    }

    await route.fulfill({
      status: mockPutStatus,
      contentType: 'application/json',
      body: JSON.stringify({
        version: 3,
        items: responseItems,
      }),
    });
  });

  await page.goto('http://localhost:8003/workspace');
  await page.evaluate(() => window.TsundokuCart.ready);

  return {
    getPutCallCount: () => putCallCount,
    getLastPutPayload: () => lastPutPayload,
  };
}

test.describe('TsundokuCart ClientId and Sync', () => {
  test('generates clientId for new items and keeps them consistent', async ({ page }) => {
    await setupStorePage(page, { activePackId: 4, initialItems: [] });

    // 新規項目を追加
    await page.evaluate(() => {
      const cart = window.TsundokuCart.load();
      cart.items.push({
        pdf_path: 'books/test.pdf',
        title: 'テスト本',
        pages: '1',
        collapsed: false,
      });
      window.TsundokuCart.save(cart);
    });

    // 保存デバウンスを待つ
    await page.waitForTimeout(500);

    const state = await page.evaluate(() => {
      const cart = window.TsundokuCart.load();
      return {
        items: cart.items,
        key: window.TsundokuCart.itemKey(cart.items[0]),
      };
    });

    expect(state.items).toHaveLength(1);
    expect(state.items[0].id).toBe(100); // サーバーが割り当てたID
    expect(state.items[0].clientId).toBeUndefined(); // IDが確定したためclientIdは不要
    expect(state.key).toBe('id:100');
  });

  test('does not overwrite concurrent local changes with server response', async ({ page }) => {
    const api = await setupStorePage(page, {
      activePackId: 4,
      initialItems: [],
      mockPutDelay: 200, // 送信遅延
    });

    // 1. 最初の項目を追加して保存開始
    await page.evaluate(() => {
      const cart = window.TsundokuCart.load();
      cart.items.push({
        pdf_path: 'books/test1.pdf',
        title: 'テスト本1',
        pages: '1',
        collapsed: false,
      });
      window.TsundokuCart.save(cart);
    });

    // デバウンスを待ち、送信開始させる
    await page.waitForTimeout(450);

    // 2. 送信中に、さらに2つ目の項目をローカルで追加（競合）
    await page.evaluate(() => {
      const cart = window.TsundokuCart.load();
      cart.items.push({
        pdf_path: 'books/test2.pdf',
        title: 'テスト本2',
        pages: '2',
        collapsed: false,
      });
      window.TsundokuCart.save(cart);
    });

    // 最初の送信完了と、2回目の送信デバウンスが走るのを待つ
    await page.waitForTimeout(800);

    const state = await page.evaluate(() => {
      return window.TsundokuCart.load();
    });

    // 巻き戻らずに2件とも保持されており、それぞれ適切なIDが割り当てられていることを確認
    expect(state.items).toHaveLength(2);
    expect(state.items[0].pdf_path).toBe('books/test1.pdf');
    expect(state.items[0].id).toBe(100);
    expect(state.items[1].pdf_path).toBe('books/test2.pdf');
    expect(state.items[1].id).toBe(101);
  });
});
