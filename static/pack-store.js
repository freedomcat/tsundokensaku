// パックのサーバ同期ストア。旧 export-cart.js（sessionStorage）の置き換え。
// TsundokuCart と同じ同期 API（load/save/...）を保ち、実体はサーバの
// アクティブパック（/api/packs）に永続化する。
// - load() はメモリキャッシュを返す（同期）
// - save() は楽観更新 + デバウンス PUT
// - 初回ロード時に sessionStorage の旧カートがあれば自動でパックへ移行する
window.TsundokuCart = (() => {
  const LEGACY_STORAGE_KEY = 'tsundokensaku-export-cart';
  const SAVE_DEBOUNCE_MS = 400;

  let cache = emptyCart();
  let activePack = null; // {id, name}
  let dirty = false;
  let saveTimer = null;
  let saveTargetPackId = null; // save() 時点の書込み先。切替後の誤書込み防止
  let initialized = false;

  function emptyCart() {
    return { version: 2, books: {} };
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  // 旧形式: { "<pdf_path>": { title, pages: [番号...] } }（versionなし）
  function migrateLegacy(parsed) {
    const cart = emptyCart();
    let migrated = false;
    for (const [path, entry] of Object.entries(parsed)) {
      if (!entry || typeof entry !== 'object' || !Array.isArray(entry.pages)) {
        continue;
      }
      const spec = window.TsundokuPages.pagesToSpec(entry.pages);
      if (!spec) {
        continue;
      }
      cart.books[path] = {
        title: typeof entry.title === 'string' && entry.title ? entry.title : path,
        pages: spec,
        collapsed: false,
        addedAt: new Date().toISOString(),
      };
      migrated = true;
    }
    return migrated ? cart : null;
  }

  function readLegacyCart() {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(LEGACY_STORAGE_KEY) || 'null');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return null;
      }
      if (parsed.version === 2 && parsed.books && typeof parsed.books === 'object') {
        return Object.keys(parsed.books).length > 0 ? parsed : null;
      }
      if (parsed.version === undefined) {
        return migrateLegacy(parsed);
      }
    } catch (error) {
      console.warn('Failed to read legacy cart', error);
    }
    return null;
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!response.ok) {
      throw new Error(`${options.method || 'GET'} ${url} -> ${response.status}`);
    }
    return response.json();
  }

  async function migrateLegacyToServer() {
    const legacy = readLegacyCart();
    if (!legacy) {
      return;
    }
    try {
      await fetchJson('/api/packs/import', {
        method: 'POST',
        body: JSON.stringify({ cart: legacy, name: '移行された資料' }),
      });
      sessionStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch (error) {
      // 失敗時は sessionStorage を残して次回リトライ
      console.warn('Failed to migrate legacy cart', error);
    }
  }

  async function fetchActivePack() {
    const listing = await fetchJson('/api/packs');
    if (listing.active_pack_id === null || listing.active_pack_id === undefined) {
      // 資料が1件もない状態。利用者が作るまで空のまま
      activePack = null;
      cache = emptyCart();
      dirty = false;
      notifyUpdated();
      return;
    }
    const pack = await fetchJson(`/api/packs/${listing.active_pack_id}`);
    const previousId = activePack ? activePack.id : null;
    activePack = { id: pack.id, name: pack.name };
    const serverCart = pack.cart && typeof pack.cart === 'object' ? pack.cart : emptyCart();
    if (dirty && (previousId === null || previousId === pack.id)) {
      // 初回取得前・同一資料の再取得では、ローカル編集を失わないよう上に重ねる
      serverCart.books = { ...serverCart.books, ...cache.books };
    } else if (dirty) {
      // 資料が切り替わった。前の資料向けの未送信編集を新しい資料へ持ち込まない
      dirty = false;
      if (saveTimer !== null) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
    }
    cache = serverCart;
    notifyUpdated();
  }

  // 資料が無ければ確認のうえ新規作成する。追加操作の前に呼ぶ。
  // 作成した/既にあれば true、利用者が取りやめたら false。
  async function ensureActivePackInteractive() {
    if (activePack) {
      return true;
    }
    if (!window.confirm('資料がありません。新しい資料を作成しますか？')) {
      return false;
    }
    const name = window.prompt('新しい資料の名前', '新しい資料');
    if (name === null) {
      return false;
    }
    try {
      await createPack(name.trim());
      return activePack !== null;
    } catch (error) {
      console.warn('Failed to create pack', error);
      return false;
    }
  }

  function notifyUpdated() {
    updateBadge();
    document.dispatchEvent(new CustomEvent('tsundoku-cart-updated'));
  }

  async function init() {
    try {
      await migrateLegacyToServer();
      await fetchActivePack();
    } catch (error) {
      console.warn('Failed to load pack from server', error);
    } finally {
      initialized = true;
      if (dirty) {
        scheduleSave();
      }
    }
  }

  function scheduleSave() {
    if (saveTimer !== null) {
      clearTimeout(saveTimer);
    }
    saveTimer = setTimeout(() => {
      saveTimer = null;
      void pushToServer();
    }, SAVE_DEBOUNCE_MS);
  }

  async function pushToServer(keepalive = false) {
    if (!dirty || !activePack) {
      return;
    }
    if (saveTargetPackId !== null && saveTargetPackId !== activePack.id) {
      // 書込み予約した資料からすでに切り替わっている。別の資料へ書かない
      dirty = false;
      return;
    }
    dirty = false;
    try {
      await fetch(`/api/packs/${activePack.id}/books`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ books: cache.books }),
        keepalive,
      });
    } catch (error) {
      dirty = true; // 次の save / focus 時にリトライ
      console.warn('Failed to save pack', error);
    }
  }

  function flushPendingSave() {
    if (saveTimer !== null) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    return pushToServer(true);
  }

  async function refresh() {
    // 書き込み待ちがある間はサーバ内容で上書きしない
    if (!initialized || dirty || saveTimer !== null) {
      return;
    }
    try {
      await fetchActivePack();
    } catch (error) {
      console.warn('Failed to refresh pack', error);
    }
  }

  function load() {
    return clone(cache);
  }

  function save(cart) {
    if (!cart || typeof cart !== 'object' || !cart.books || typeof cart.books !== 'object') {
      return;
    }
    cache = clone(cart);
    dirty = true;
    saveTargetPackId = activePack ? activePack.id : null;
    updateBadge();
    if (initialized && activePack) {
      scheduleSave();
    }
  }

  function bookCount(cart) {
    return Object.keys(cart.books).length;
  }

  function totalPages(cart) {
    let total = 0;
    for (const entry of Object.values(cart.books)) {
      const count = window.TsundokuPages.countPages(entry.pages);
      if (count === null) {
        return null;
      }
      total += count;
    }
    return total;
  }

  function summaryLabel(cart) {
    const books = bookCount(cart);
    const pages = totalPages(cart);
    return pages === null ? `${books}冊` : `${books}冊 / ${pages}ページ`;
  }

  function updateBadge() {
    const badge = document.getElementById('nav-workspace-count');
    if (!badge) {
      return;
    }
    const count = bookCount(cache);
    badge.textContent = `${count}冊`;
    badge.hidden = count === 0;
  }

  function getActivePack() {
    return activePack ? { ...activePack } : null;
  }

  async function listPacks() {
    return fetchJson('/api/packs');
  }

  async function createPack(name) {
    await flushPendingSave();
    const pack = await fetchJson('/api/packs', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    await fetchActivePack();
    return pack;
  }

  async function activatePack(packId) {
    await flushPendingSave();
    await fetchJson(`/api/packs/${packId}/activate`, { method: 'POST' });
    await fetchActivePack();
  }

  // 現在の資料を意図的に未選択にする（資料自体・中身は削除しない）
  async function deactivatePack() {
    await flushPendingSave();
    await fetchJson('/api/packs/deactivate', { method: 'POST' });
    await fetchActivePack();
  }

  async function renamePack(packId, name) {
    const pack = await fetchJson(`/api/packs/${packId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    });
    if (activePack && activePack.id === packId) {
      activePack = { id: pack.id, name: pack.name };
      notifyUpdated();
    }
    return pack;
  }

  async function deletePack(packId) {
    const result = await fetchJson(`/api/packs/${packId}`, { method: 'DELETE' });
    await fetchActivePack();
    return result;
  }

  window.addEventListener('focus', () => void refresh());
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
      void refresh();
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      flushPendingSave();
    }
  });
  window.addEventListener('pagehide', flushPendingSave);

  const ready = init();

  return {
    load,
    save,
    bookCount,
    totalPages,
    summaryLabel,
    updateBadge,
    getActivePack,
    ensureActivePackInteractive,
    listPacks,
    createPack,
    activatePack,
    deactivatePack,
    renamePack,
    deletePack,
    refresh,
    ready,
  };
})();
