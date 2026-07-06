// エクスポートカート（ワークスペース）の sessionStorage ストア。
// 形式: { version: 2, books: { "<pdf_path>": { title, pages(spec文字列), collapsed, addedAt } } }
window.TsundokuCart = (() => {
  const STORAGE_KEY = 'tsundokensaku-export-cart';

  function emptyCart() {
    return { version: 2, books: {} };
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

  function load() {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null');
      if (
        parsed &&
        typeof parsed === 'object' &&
        parsed.version === 2 &&
        parsed.books &&
        typeof parsed.books === 'object' &&
        !Array.isArray(parsed.books)
      ) {
        return parsed;
      }
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && parsed.version === undefined) {
        const migrated = migrateLegacy(parsed);
        if (migrated) {
          sessionStorage.setItem(STORAGE_KEY, JSON.stringify(migrated));
          return migrated;
        }
      }
    } catch (error) {
      console.warn('Failed to read export cart', error);
    }
    return emptyCart();
  }

  function save(cart) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(cart));
    } catch (error) {
      console.warn('Failed to save export cart', error);
    }
    updateBadge();
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
    const count = bookCount(load());
    badge.textContent = String(count);
    badge.hidden = count === 0;
  }

  return { load, save, bookCount, totalPages, summaryLabel, updateBadge };
})();

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => window.TsundokuCart.updateBadge());
} else {
  window.TsundokuCart.updateBadge();
}
