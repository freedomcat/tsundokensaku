// パックのサーバ同期ストア。旧 export-cart.js（sessionStorage）の置き換え。
// TsundokuCart と同じ同期 API（load/save/...）を保ち、実体はサーバの
// アクティブパック（/api/packs）に永永永化する。
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
    return { version: 3, items: [] };
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function entryToItem(path, entry) {
    return {
      pdf_path: path,
      title: typeof entry.title === 'string' && entry.title ? entry.title : path,
      pages: typeof entry.pages === 'string'
        ? entry.pages
        : (Array.isArray(entry.pages) ? window.TsundokuPages.pagesToSpec(entry.pages) : ''),
      collapsed: Boolean(entry.collapsed),
      addedAt: typeof entry.addedAt === 'string' && entry.addedAt ? entry.addedAt : new Date().toISOString(),
    };
  }

  function booksToCart(books) {
    const cart = emptyCart();
    for (const [path, entry] of Object.entries(books || {})) {
      if (!entry || typeof entry !== 'object') {
        continue;
      }
      const item = entryToItem(path, entry);
      if (item.pages) {
        cart.items.push(item);
      }
    }
    return cart;
  }

  function normalizeCart(value) {
    if (!value || typeof value !== 'object') {
      return emptyCart();
    }
    if (value.version === 3 && Array.isArray(value.items)) {
      return {
        version: 3,
        items: value.items
          .filter((item) => item && typeof item === 'object' && typeof item.pdf_path === 'string' && item.pdf_path)
          .map((item) => ({
            id: Number.isInteger(item.id) ? item.id : undefined,
            clientId: typeof item.clientId === 'string' && item.clientId
              ? item.clientId
              : (Number.isInteger(item.id) ? undefined : createClientId()),
            pdf_path: item.pdf_path,
            title: typeof item.title === 'string' && item.title ? item.title : item.pdf_path,
            pages: typeof item.pages === 'string' ? item.pages : '',
            collapsed: Boolean(item.collapsed),
            addedAt: typeof item.addedAt === 'string' && item.addedAt ? item.addedAt : new Date().toISOString(),
          })),
      };
    }
    if (value.books && typeof value.books === 'object') {
      return booksToCart(value.books);
    }
    return emptyCart();
  }

  function createClientId() {
    return `new:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  }

  function itemKey(item) {
    if (Number.isInteger(item.id)) {
      return `id:${item.id}`;
    }
    if (!item.clientId) {
      item.clientId = createClientId();
    }
    return item.clientId;
  }

  function cartForSave(cart) {
    return {
      items: cart.items.map((item) => ({
        ...(Number.isInteger(item.id) ? { id: item.id } : {}),
        pdf_path: item.pdf_path,
        title: item.title,
        pages: item.pages,
        collapsed: Boolean(item.collapsed),
        addedAt: item.addedAt,
      })),
    };
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
      cart.items.push({ pdf_path: path, title: typeof entry.title === 'string' && entry.title ? entry.title : path, pages: spec, collapsed: false, addedAt: new Date().toISOString() });
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
      if (parsed.version === 3 && Array.isArray(parsed.items)) {
        return parsed.items.length > 0 ? normalizeCart(parsed) : null;
      }
      if (parsed.version === 2 && parsed.books && typeof parsed.books === 'object') {
        return Object.keys(parsed.books).length > 0 ? booksToCart(parsed.books) : null;
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
    const serverCart = normalizeCart(pack.version === 3 && Array.isArray(pack.items) ? pack : pack.cart);
    if (dirty && (previousId === null || previousId === pack.id)) {
      // 初回取得前・同一資料の再取得では、ローカル編集を失わないよう上に重ねる
      const byKey = new Map();
      for (const item of serverCart.items) {
        byKey.set(itemKey(item), item);
      }
      for (const item of cache.items) {
        byKey.set(itemKey(item), item);
      }
      serverCart.items = [...byKey.values()];
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
    const currentSavingPackId = activePack.id;
    const currentSavingItems = clone(cache.items);
    dirty = false;
    try {
      const response = await fetch(`/api/packs/${currentSavingPackId}/items`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cartForSave(cache)),
        keepalive,
      });
      if (response.ok) {
        const payload = await response.json();
        if (!activePack || activePack.id !== currentSavingPackId) {
          return;
        }
        const serverCart = normalizeCart(payload);
        if (dirty) {
          const idMap = new Map();
          for (let i = 0; i < currentSavingItems.length; i++) {
            const originalItem = currentSavingItems[i];
            const serverItem = serverCart.items[i];
            if (serverItem && !Number.isInteger(originalItem.id) && Number.isInteger(serverItem.id)) {
              idMap.set(itemKey(originalItem), serverItem.id);
            }
          }
          for (const item of cache.items) {
            const key = itemKey(item);
            if (idMap.has(key)) {
              item.id = idMap.get(key);
            }
          }
        } else {
          cache = serverCart;
        }
        notifyUpdated();
      } else {
        throw new Error(`PUT /api/packs/${activePack.id}/items -> ${response.status}`);
      }
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
    if (!cart || typeof cart !== 'object') {
      return;
    }
    cache = normalizeCart(cart);
    dirty = true;
    saveTargetPackId = activePack ? activePack.id : null;
    updateBadge();
    if (initialized && activePack) {
      scheduleSave();
    }
  }

  function bookCount(cart) {
    return normalizeCart(cart).items.length;
  }

  function totalPages(cart) {
    let total = 0;
    for (const entry of normalizeCart(cart).items) {
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
    return pages === null ? `${books}件` : `${books}件 / ${pages}ページ`;
  }

  function updateBadge() {
    const badge = document.getElementById('nav-workspace-count');
    if (!badge) {
      return;
    }
    // 資料棚・資料一覧の「冊数」と揃え、同じPDFを複数項目に追加しても1冊と数える。
    const count = new Set(normalizeCart(cache).items.map((item) => item.pdf_path)).size;
    badge.textContent = `${count}件`;
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
    try {
      await flushPendingSave();
    } catch (err) {
      console.warn('Failed to flush pending save before activating pack', err);
    }
    await fetchJson(`/api/packs/${packId}/activate`, { method: 'POST' });
    await fetchActivePack();
  }

  // 現在の資料を意図的に未選択にする（資料自体・中身は削除しない）
  async function deactivatePack() {
    try {
      await flushPendingSave();
    } catch (err) {
      console.warn('Failed to flush pending save before deactivating pack', err);
    }
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
    itemKey,
    createClientId,
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
    flushPendingSave,
    ready,
  };
})();
