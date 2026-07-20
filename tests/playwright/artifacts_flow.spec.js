const { test, expect } = require('@playwright/test');

function artifact(id = 1) {
  return {
    id,
    title: 'AIノートのタイトル',
    body: '長い本文\n出典を含む内容です。',
    source_service: 'ChatGPT',
    source_model: 'gpt-test',
    prompt: '要点を整理してください',
    export_event_id: 10,
    pack_id: 7,
    pack_name: 'テスト資料',
    created_at: '2026-07-20T00:00:00+00:00',
    updated_at: '2026-07-20T00:00:00+00:00',
    source_count: 1,
    sources: [{ id: 1, artifact_id: id, pdf_path: 'books/a.pdf', title: '出典の本', pages: '3-4', position: 0 }],
  };
}

async function mockArtifactApi(
  page,
  { postStatus = 201, deleteStatus = 200, initialArtifacts = [] } = {},
) {
  let artifacts = [...initialArtifacts];
  let deleteCallCount = 0;
  await page.route('**/api/export-events?limit=20', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ export_events: [{ id: 10, pack_name: 'テスト資料', profile: 'standard', format: 'pdf', exported_at: '2026-07-20T00:00:00+00:00', items: [{ pdf_path: 'books/a.pdf', title: '出典の本', pages: '3-4', position: 0 }] }] }) });
  });
  await page.route('**/api/artifacts**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const id = Number(url.pathname.split('/').pop());
    if (request.method() === 'GET' && url.pathname === '/api/artifacts') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ artifacts: artifacts.map(({ body, prompt, sources, ...summary }) => summary) }) }); return;
    }
    if (request.method() === 'GET') {
      const current = artifacts.find((item) => item.id === id);
      await route.fulfill({ status: current ? 200 : 404, contentType: 'application/json', body: JSON.stringify(current || { detail: 'AIノートが見つかりません' }) }); return;
    }
    if (request.method() === 'POST') {
      if (postStatus !== 201) { await route.fulfill({ status: postStatus, contentType: 'application/json', body: JSON.stringify({ detail: 'デモモードのため無効です' }) }); return; }
      const payload = JSON.parse(request.postData() || '{}'); const created = { ...artifact(1), ...payload, source_count: payload.export_event_id ? 1 : 0, sources: payload.export_event_id ? artifact(1).sources : [] };
      artifacts = [created]; await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(created) }); return;
    }
    if (request.method() === 'DELETE') {
      deleteCallCount += 1;
      if (deleteStatus !== 200) { await route.fulfill({ status: deleteStatus, contentType: 'application/json', body: JSON.stringify({ detail: 'デモモードのため無効です' }) }); return; }
      artifacts = artifacts.filter((item) => item.id !== id); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: id }) });
    }
  });
  return { getDeleteCallCount: () => deleteCallCount };
}

async function createActivePack(page) {
  const packName = `Phase4A3 E2E資料-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  await page.goto('http://localhost:8003/workspace');
  await page.evaluate(async (name) => {
    const response = await fetch('/api/packs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!response.ok) throw new Error(`資料の作成に失敗しました: ${response.status}`);
    const pack = await response.json();
    await window.TsundokuCart.activatePack(pack.id);
  }, packName);
  return packName;
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

async function createExportEvent(page) {
  await page.getByRole('button', { name: '書き出す', exact: true }).click();
  const modal = page.locator('#ws-export-modal');
  await expect(modal).toHaveClass(/open/);
  await modal.getByRole('radio', { name: /PDF一式/ }).check();
  const downloadPromise = page.waitForEvent('download');
  await modal.getByRole('button', { name: '書き出す', exact: true }).click();
  const download = await downloadPromise;
  expect(await download.failure()).toBeNull();
}

test('opens AI notes from navigation and displays the empty state', async ({ page }) => {
  await mockArtifactApi(page);
  await page.goto('http://localhost:8003/workspace');
  await page.getByRole('link', { name: 'AIノート' }).click();
  await expect(page).toHaveURL(/\/artifacts$/);
  await expect(page.locator('#artifact-empty')).toContainText('AIノートはまだありません');
});

test('creates, displays, opens, and deletes an AI note with export sources', async ({ page }) => {
  await mockArtifactApi(page);
  await page.goto('http://localhost:8003/artifacts');
  await page.locator('#artifact-create-open').click();
  await expect(page.locator('#artifact-export-event')).toContainText('テスト資料');
  await page.locator('#artifact-title').fill('AIノートのタイトル');
  await page.locator('#artifact-body').fill('長い本文\n出典を含む内容です。');
  await page.locator('#artifact-source-service').fill('ChatGPT');
  await page.locator('#artifact-source-model').fill('gpt-test');
  await page.locator('#artifact-prompt').fill('要点を整理してください');
  await page.locator('#artifact-export-event').selectOption('10');
  await page.locator('#artifact-create-form').evaluate((form) => form.requestSubmit());
  await expect(page.locator('#artifact-list')).toContainText('AIノートのタイトル');
  await page.getByRole('button', { name: '詳細を開く' }).click();
  await expect(page.locator('#artifact-detail-body')).toContainText('長い本文');
  await expect(page.locator('#artifact-detail-body')).toContainText('出典の本 / p.3-4');
  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#artifact-delete').click();
  await expect(page.locator('#artifact-empty')).toBeVisible();
});

test('shows validation and API 403 failures without pretending to save', async ({ page }) => {
  await mockArtifactApi(page, { postStatus: 403 });
  await page.goto('http://localhost:8003/artifacts');
  await page.locator('#artifact-create-open').click();
  await page.locator('#artifact-title').fill(' ');
  await page.locator('#artifact-body').fill(' ');
  await page.locator('#artifact-create-form').evaluate((form) => form.requestSubmit());
  await expect(page.locator('#artifact-create-status')).toContainText('タイトルと本文を入力してください');
  await page.locator('#artifact-title').fill('保存されないノート');
  await page.locator('#artifact-body').fill('本文');
  await page.locator('#artifact-create-form').evaluate((form) => form.requestSubmit());
  await expect(page.locator('#artifact-create-status')).toContainText('デモモードのため無効です');
  await expect(page.locator('#artifact-create-modal')).toHaveClass(/open/);
  await expect(page.locator('#artifact-list')).toBeEmpty();
});

test('keeps an AI note visible when the DELETE API returns 403 (error handling mock)', async ({ page }) => {
  const api = await mockArtifactApi(page, {
    deleteStatus: 403,
    initialArtifacts: [artifact()],
  });
  await page.goto('http://localhost:8003/artifacts');
  await page.getByRole('button', { name: '詳細を開く' }).click();
  const detailModal = page.locator('#artifact-detail-modal');
  await expect(detailModal).toHaveClass(/open/);

  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#artifact-delete').click();

  await expect(page.locator('#artifact-status')).toContainText('デモモードのため無効です');
  await expect(detailModal).toHaveClass(/open/);
  await expect(page.locator('#artifact-list')).toContainText('AIノートのタイトル');
  expect(api.getDeleteCallCount()).toBe(1);
});

test('creates, displays, and deletes an AI note through the real export, API, and DB flow', async ({ page }) => {
  const packName = await createActivePack(page);
  await addOneBookToActivePack(page);
  await createExportEvent(page);

  const title = `Phase4A3 AIノート-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const body = '実バックエンドを通じて保存したAIノート本文です。';
  const exportEvent = await page.evaluate(async (name) => {
    const response = await fetch('/api/export-events?limit=20');
    const payload = await response.json();
    return payload.export_events.find((event) => event.pack_name === name);
  }, packName);
  expect(exportEvent).toBeTruthy();
  expect(exportEvent.items).toHaveLength(1);
  let artifactId = null;

  try {
    await page.goto('http://localhost:8003/artifacts');
    await page.locator('#artifact-create-open').click();
    const eventSelect = page.locator('#artifact-export-event');
    const eventOption = eventSelect.locator('option', { hasText: packName });
    await expect(eventOption).toHaveCount(1);

    await page.locator('#artifact-title').fill(title);
    await page.locator('#artifact-body').fill(body);
    await page.locator('#artifact-source-service').fill('ChatGPT');
    await page.locator('#artifact-source-model').fill('E2E model');
    await page.locator('#artifact-prompt').fill('出典を保ったまま要約してください。');
    await eventSelect.selectOption(await eventOption.getAttribute('value'));

    const createResponse = page.waitForResponse((response) => (
      new URL(response.url()).pathname === '/api/artifacts'
      && response.request().method() === 'POST'
      && response.status() === 201
    ));
    await page.locator('#artifact-create-submit').click();
    const created = await (await createResponse).json();
    artifactId = created.id;

    const artifactRow = page.locator('.artifact-row', { hasText: title });
    await expect(artifactRow).toBeVisible();
    await artifactRow.getByRole('button', { name: '詳細を開く' }).click();
    await expect(page.locator('#artifact-detail-modal')).toHaveClass(/open/);
    await expect(page.locator('#artifact-detail-body')).toContainText(body);
    await expect(page.locator('.artifact-source-list li')).toContainText(exportEvent.items[0].title);

    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('#artifact-delete').click();
    await expect(page.locator('#artifact-detail-modal')).not.toHaveClass(/open/);
    await expect(page.locator('.artifact-row', { hasText: title })).toHaveCount(0);
    artifactId = null;
  } finally {
    if (artifactId !== null) {
      await page.evaluate(async (id) => {
        await fetch(`/api/artifacts/${id}`, { method: 'DELETE' });
      }, artifactId);
    }
  }
});
