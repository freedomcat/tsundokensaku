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
        body: JSON.stringify({ cart: legacy, name: '移行されたワークスペース' }),
      });
      sessionStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch (error) {
      // 失敗時は sessionStorage を残して次回リトライ
      console.warn('Failed to migrate legacy cart', error);
    }
  }

  async function fetchActivePack() {
    const listing = await fetchJson('/api/packs');
    const pack = await fetchJson(`/api/packs/${listing.active_pack_id}`);
    activePack = { id: pack.id, name: pack.name };
    const serverCart = pack.cart && typeof pack.cart === 'object' ? pack.cart : emptyCart();
    if (dirty) {
      // 取得完了前のローカル編集を失わないよう、サーバ内容の上にローカルを重ねる
      serverCart.books = { ...serverCart.books, ...cache.books };
    }
    cache = serverCart;
    notifyUpdated();
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
    void pushToServer(true);
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
    flushPendingSave();
    const pack = await fetchJson('/api/packs', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    await fetchActivePack();
    return pack;
  }

  async function activatePack(packId) {
    flushPendingSave();
    await fetchJson(`/api/packs/${packId}/activate`, { method: 'POST' });
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
    listPacks,
    createPack,
    activatePack,
    renamePack,
    deletePack,
    refresh,
    ready,
  };
})();
