// PDFプレビューモーダルの制御（開閉・章選択・切出・共有・ページ選択サムネイル）。
// #pdf-modal 系DOMは templates/base.html 側に定義されている。
(() => {
  const modal = document.getElementById('pdf-modal');
  const modalPanel = modal?.querySelector('.modal-panel');
  const frame = document.getElementById('pdf-modal-frame');
  const title = document.getElementById('pdf-modal-title');
  const pagesInput = document.getElementById('pdf-modal-pages');
  const chaptersWrap = document.getElementById('pdf-modal-chapters-wrap');
  const chaptersMenu = document.getElementById('pdf-modal-chapters');
  const chaptersSummary = document.getElementById('pdf-modal-chapters-summary');
  const exportLink = document.getElementById('pdf-modal-export');
  const exportMdLink = document.getElementById('pdf-modal-export-md');
  const addWorkspaceButton = document.getElementById('pdf-modal-add-workspace');
  const exportStatus = document.getElementById('pdf-modal-export-status');
  const fullscreenButton = document.getElementById('pdf-modal-fullscreen');
  const openLink = document.getElementById('pdf-modal-open');
  const shareMenu = document.getElementById('pdf-modal-share-menu');
  const shareDetails = modal?.querySelector('.modal-share');
  const scrapboxLink = document.getElementById('pdf-modal-scrapbox');
  const closeButton = document.getElementById('pdf-modal-close');
  const browseBar = document.getElementById('pdf-modal-browse-bar');
  const thumbBar = document.getElementById('pdf-modal-thumb-bar');
  const thumbToggleButton = document.getElementById('pdf-modal-thumb-toggle');
  const thumbBackButton = document.getElementById('pdf-modal-thumb-back');
  const thumbHeading = document.getElementById('pdf-modal-thumb-heading');
  const thumbPanel = document.getElementById('pdf-modal-thumb-panel');
  const thumbGrid = document.getElementById('pdf-modal-thumb-grid');
  const thumbPrevButton = document.getElementById('pdf-modal-thumb-prev');
  const thumbNextButton = document.getElementById('pdf-modal-thumb-next');
  const thumbApplyButton = document.getElementById('pdf-modal-thumb-apply');
  const thumbStatus = document.getElementById('pdf-modal-thumb-status');
  const thumbDetailOverlay = document.getElementById('pdf-modal-thumb-overlay');
  const thumbDetailTitle = document.getElementById('pdf-modal-thumb-detail-title');
  const thumbDetailBody = document.getElementById('pdf-modal-thumb-detail-body');
  const thumbDetailPrevButton = document.getElementById('pdf-modal-thumb-detail-prev');
  const thumbDetailNextButton = document.getElementById('pdf-modal-thumb-detail-next');
  const thumbDetailCloseButton = document.getElementById('pdf-modal-thumb-detail-close');
  let currentPdfPath = '';

  function canUseWebShare() {
    return typeof navigator !== 'undefined' && typeof navigator.share === 'function';
  }

  function renderShareMenu() {
    if (!shareMenu) {
      return;
    }
    // 共有は「切出」メニュー内の1項目。使えない環境・ページ未指定時は項目ごと出さない
    if (!canUseWebShare() || !currentPdfPath || !pagesInput.value.trim()) {
      shareMenu.innerHTML = '';
      return;
    }
    shareMenu.innerHTML = `
      <button class="modal-share-link" type="button" id="pdf-modal-native-share">
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path fill="currentColor" d="M18 16a3 3 0 0 0-2.24 1.01l-6.16-3.08a3.1 3.1 0 0 0 0-2.86l6.16-3.08A3 3 0 1 0 15 6c0 .27.04.53.11.78L9.08 9.86a3 3 0 1 0 0 4.28l6.03 3.08A3 3 0 1 0 18 16Z"/>
        </svg>
        <span>指定ページPDFを共有</span>
      </button>
    `;
    document.getElementById('pdf-modal-native-share')?.addEventListener('click', () => {
      void shareExportedPdf();
    });
  }

  function updateFullscreenButton() {
    if (!fullscreenButton || !modalPanel) {
      return;
    }
    fullscreenButton.textContent = document.fullscreenElement === modalPanel ? '全画面を終了' : '全画面';
  }

  async function exitFullscreenIfNeeded() {
    if (document.fullscreenElement === modalPanel && document.exitFullscreen) {
      try {
        await document.exitFullscreen();
      } catch (error) {
        console.warn('Failed to exit fullscreen', error);
      }
    }
  }

  async function toggleFullscreen() {
    if (!modalPanel) {
      return;
    }
    if (document.fullscreenElement === modalPanel) {
      await exitFullscreenIfNeeded();
      return;
    }
    if (modalPanel.requestFullscreen) {
      try {
        await modalPanel.requestFullscreen();
      } catch (error) {
        console.warn('Failed to enter fullscreen', error);
      }
    }
  }

  async function closeModal() {
    await exitFullscreenIfNeeded();
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    frame.src = 'about:blank';
    modal.querySelector('.modal-share')?.removeAttribute('open');
    chaptersWrap?.removeAttribute('open');
    resetThumbPanel();
    updateFullscreenButton();
  }

  function normalizeExternalUrl(url) {
    const normalized = String(url || '').trim();
    if (!normalized || ['none', 'null', 'undefined'].includes(normalized.toLowerCase())) {
      return '';
    }
    try {
      const parsed = new URL(normalized, window.location.origin);
      return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
    } catch (error) {
      return '';
    }
  }

  function setScrapboxLink(url) {
    const normalizedUrl = normalizeExternalUrl(url);
    if (normalizedUrl) {
      scrapboxLink.href = normalizedUrl;
      scrapboxLink.hidden = false;
    } else {
      scrapboxLink.hidden = true;
      scrapboxLink.removeAttribute('href');
    }
  }

  function setExportLink() {
    const pages = pagesInput.value.trim();
    const validationError = currentPdfPath && pages ? TsundokuPages.validatePageSpec(pages, currentPageCount) : '';
    const usable = Boolean(currentPdfPath && pages && !validationError);
    const links = [
      [exportLink, '/export-pdf'],
      [exportMdLink, '/export-md'],
    ];
    for (const [link, endpoint] of links) {
      if (!link) {
        continue;
      }
      if (!usable) {
        link.href = '#';
        link.removeAttribute('target');
        link.setAttribute('aria-disabled', 'true');
        continue;
      }
      const params = new URLSearchParams({
        pdf_path: currentPdfPath,
        pages,
      });
      link.href = `${endpoint}?${params.toString()}`;
      link.target = '_blank';
      link.removeAttribute('aria-disabled');
    }
    if (addWorkspaceButton) {
      addWorkspaceButton.disabled = !usable;
    }
    setExportStatus(validationError, true);
    renderShareMenu();
  }

  let chaptersRequestToken = 0;
  let currentPackItemKey = null;
  let currentPageCount = null;

  // --- ページを選ぶ（現在ページ周辺のサムネイル） ---
  const THUMB_INITIAL_RADIUS = 10; // 初期表示: 現在ページの前後何ページ分を読み込むか
  const THUMB_EXPAND_STEP = 10; // 「前/次の10ページを表示」1回あたりの拡張ページ数
  const THUMB_DETAIL_CACHE_LIMIT = 20;
  let thumbRequestToken = 0;
  let thumbLoadedPages = new Set();
  let thumbSelectedPages = new Set();
  let thumbLoadedMin = null; // ロード済み範囲の最小ページ番号（未初期化は null）
  let thumbLoadedMax = null; // ロード済み範囲の最大ページ番号
  let thumbAnchorPage = 1; // 起点にした「現在ページ」（見出し・マーカー表示用）
  let thumbLoadingDirection = null; // 'prev' | 'next' | null（前後ボタンの多重クリック防止）
  let thumbDetailCache = new Map();
  let thumbDetailAbortController = null;
  let thumbDetailRequestToken = 0;
  let thumbDetailActivePage = null;
  let thumbDetailReturnFocus = null;

  function extractPageFromUrl(url) {
    const match = /#page=(\d+)/.exec(url || '');
    return match ? parseInt(match[1], 10) : null;
  }

  // spec文字列の最初の閉区間の開始ページを返す。開区間のみ・空なら null
  function firstPageOfSpec(spec) {
    const closed = TsundokuPages.specToIntervals(spec || '').find((interval) => !interval.open);
    return closed ? closed.start : null;
  }

  function setThumbStatus(message, isError = false) {
    if (!thumbStatus) {
      return;
    }
    thumbStatus.textContent = message || '';
    thumbStatus.classList.toggle('is-error', Boolean(isError && message));
  }

  function setThumbDetailTitle(pageNumber = null) {
    if (!thumbDetailTitle) {
      return;
    }
    thumbDetailTitle.textContent = pageNumber ? `p.${pageNumber} 拡大プレビュー` : '拡大プレビュー';
  }

  function thumbDetailCacheKey(pageNumber) {
    return `${currentPdfPath}:${pageNumber}:detail`;
  }

  function getCachedThumbDetail(pageNumber) {
    const key = thumbDetailCacheKey(pageNumber);
    if (!thumbDetailCache.has(key)) {
      return null;
    }
    const data = thumbDetailCache.get(key);
    thumbDetailCache.delete(key);
    thumbDetailCache.set(key, data);
    return data;
  }

  function setCachedThumbDetail(pageNumber, data) {
    const key = thumbDetailCacheKey(pageNumber);
    if (thumbDetailCache.has(key)) {
      thumbDetailCache.delete(key);
    }
    thumbDetailCache.set(key, data);
    while (thumbDetailCache.size > THUMB_DETAIL_CACHE_LIMIT) {
      const oldestKey = thumbDetailCache.keys().next().value;
      thumbDetailCache.delete(oldestKey);
    }
  }

  function updateThumbDetailNav() {
    if (thumbDetailPrevButton) {
      thumbDetailPrevButton.disabled = !currentPageCount || !thumbDetailActivePage || thumbDetailActivePage <= 1;
    }
    if (thumbDetailNextButton) {
      thumbDetailNextButton.disabled = !currentPageCount || !thumbDetailActivePage || thumbDetailActivePage >= currentPageCount;
    }
  }

  function isThumbDetailOverlayOpen() {
    return Boolean(thumbDetailOverlay && !thumbDetailOverlay.hidden);
  }

  function openThumbDetailOverlay(pageNumber, returnFocus = null) {
    if (!thumbDetailOverlay) {
      return;
    }
    thumbDetailReturnFocus = returnFocus || document.activeElement;
    thumbDetailOverlay.hidden = false;
    requestAnimationFrame(() => {
      thumbDetailOverlay.classList.add('is-open');
    });
    document.body.classList.add('modal-thumb-overlay-open');
    thumbPanel?.classList.add('detail-open');
    setThumbDetailTitle(pageNumber);
    updateThumbDetailNav();
    thumbDetailCloseButton?.focus();
  }

  function closeThumbDetailOverlay({ clearCache = false } = {}) {
    thumbDetailRequestToken += 1;
    thumbDetailAbortController?.abort();
    thumbDetailAbortController = null;
    thumbDetailActivePage = null;
    if (clearCache) {
      thumbDetailCache.clear();
    }
    if (thumbDetailOverlay) {
      thumbDetailOverlay.classList.remove('is-open');
      thumbDetailOverlay.hidden = true;
    }
    document.body.classList.remove('modal-thumb-overlay-open');
    thumbPanel?.classList.remove('detail-open');
    updateThumbDetailNav();
    if (thumbDetailReturnFocus && typeof thumbDetailReturnFocus.focus === 'function') {
      thumbDetailReturnFocus.focus();
    }
    thumbDetailReturnFocus = null;
  }

  function setThumbDetailMessage(message, { pageNumber = null, retry = false, isError = false } = {}) {
    if (!thumbDetailBody) {
      return;
    }
    setThumbDetailTitle(pageNumber);
    thumbDetailBody.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'modal-thumb-dialog-message';
    wrap.textContent = message;
    if (isError) {
      wrap.classList.add('is-error');
    }
    if (retry && pageNumber) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'modal-thumb-dialog-retry';
      button.textContent = '再試行';
      button.addEventListener('click', () => {
        void loadThumbDetail(pageNumber, { force: true });
      });
      wrap.appendChild(document.createElement('br'));
      wrap.appendChild(button);
    }
    thumbDetailBody.appendChild(wrap);
    updateThumbDetailNav();
  }

  function setThumbDetailImage(pageNumber, base64Data) {
    if (!thumbDetailBody) {
      return;
    }
    setThumbDetailTitle(pageNumber);
    thumbDetailBody.innerHTML = '';
    const img = document.createElement('img');
    img.src = `data:image/jpeg;base64,${base64Data}`;
    img.alt = `p.${pageNumber} 拡大プレビュー`;
    thumbDetailBody.appendChild(img);
    updateThumbDetailNav();
  }

  function resetThumbDetail() {
    closeThumbDetailOverlay({ clearCache: true });
    if (thumbDetailBody) {
      thumbDetailBody.innerHTML = '';
    }
  }

  async function loadThumbDetail(pageNumber, { force = false, returnFocus = null } = {}) {
    if (!currentPdfPath || !pageNumber) {
      return;
    }
    openThumbDetailOverlay(pageNumber, returnFocus);
    thumbDetailActivePage = pageNumber;
    const cachedData = getCachedThumbDetail(pageNumber);
    if (!force && cachedData) {
      setThumbDetailImage(pageNumber, cachedData);
      return;
    }
    thumbDetailAbortController?.abort();
    const controller = new AbortController();
    thumbDetailAbortController = controller;
    const token = ++thumbDetailRequestToken;
    setThumbDetailMessage('拡大プレビューを読み込み中...', { pageNumber });
    try {
      const response = await fetch(
        `/pdf-thumbnails?${new URLSearchParams({
          pdf_path: currentPdfPath,
          pages: String(pageNumber),
          size: 'detail',
        })}`,
        { signal: controller.signal },
      );
      if (token !== thumbDetailRequestToken || thumbDetailActivePage !== pageNumber) {
        return;
      }
      if (!response.ok) {
        setThumbDetailMessage('拡大プレビューの取得に失敗しました', {
          pageNumber,
          retry: true,
          isError: true,
        });
        return;
      }
      const payload = await response.json();
      const item = Array.isArray(payload.pages) ? payload.pages.find((page) => page.page === pageNumber) : null;
      if (token !== thumbDetailRequestToken || thumbDetailActivePage !== pageNumber) {
        return;
      }
      if (!item?.data) {
        setThumbDetailMessage('このページの拡大プレビューを表示できませんでした', {
          pageNumber,
          retry: true,
          isError: true,
        });
        return;
      }
      setCachedThumbDetail(pageNumber, item.data);
      setThumbDetailImage(pageNumber, item.data);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      if (token === thumbDetailRequestToken) {
        setThumbDetailMessage('拡大プレビューの取得に失敗しました', {
          pageNumber,
          retry: true,
          isError: true,
        });
      }
      console.warn('Failed to load detail thumbnail', error);
    }
  }

  function moveThumbDetail(delta) {
    if (!currentPageCount || !thumbDetailActivePage) {
      return;
    }
    const nextPage = thumbDetailActivePage + delta;
    if (nextPage < 1 || (currentPageCount && nextPage > currentPageCount)) {
      return;
    }
    void loadThumbDetail(nextPage, { returnFocus: thumbDetailReturnFocus });
  }

  function handleThumbDetailKeydown(event) {
    if (!thumbDetailOverlay || thumbDetailOverlay.hidden) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeThumbDetailOverlay();
      return;
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      moveThumbDetail(-1);
      return;
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      moveThumbDetail(1);
      return;
    }
    if (event.key === 'Tab') {
      const focusables = [...thumbDetailOverlay.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
        .filter((element) => !element.disabled && element.offsetParent !== null);
      if (focusables.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  // 資料棚の特定カードから開いた場合だけ item_key を持ち、既存項目を更新する。
  // 検索結果など item_key なしの文脈では新規追加として扱う。
  // 呼び出し側は事前に TsundokuCart.ready を await しておくこと
  function currentCartEntry() {
    if (!currentPackItemKey) {
      return null;
    }
    const cart = TsundokuCart.load();
    const items = Array.isArray(cart.items) ? cart.items : [];
    return items.find((item) => TsundokuCart.itemKey(item) === currentPackItemKey) || null;
  }

  function updateThumbApplyButton() {
    if (!thumbApplyButton) {
      return;
    }
    const count = thumbSelectedPages.size;
    const inCart = Boolean(currentCartEntry());
    thumbApplyButton.textContent = inCart ? `${count}ページを資料に反映` : `${count}ページを選択`;
    thumbApplyButton.disabled = count === 0;
  }

  function updateThumbRangeButtons() {
    if (thumbPrevButton) {
      const canGoPrev = thumbLoadedMin !== null && thumbLoadedMin > 1;
      thumbPrevButton.hidden = thumbLoadedMin === null;
      thumbPrevButton.disabled = !canGoPrev || thumbLoadingDirection !== null;
    }
    if (thumbNextButton) {
      const canGoNext = thumbLoadedMax !== null && (!currentPageCount || thumbLoadedMax < currentPageCount);
      thumbNextButton.hidden = thumbLoadedMax === null;
      thumbNextButton.disabled = !canGoNext || thumbLoadingDirection !== null;
    }
  }

  function enterThumbMode() {
    if (!thumbPanel) {
      return;
    }
    thumbPanel.hidden = false;
    frame.hidden = true;
    if (browseBar) {
      browseBar.hidden = true;
    }
    if (thumbBar) {
      thumbBar.hidden = false;
    }
    void ensureThumbPanelInitialized();
  }

  function exitThumbMode() {
    if (!thumbPanel) {
      return;
    }
    closeThumbDetailOverlay({ clearCache: true });
    thumbPanel.hidden = true;
    frame.hidden = false;
    if (browseBar) {
      browseBar.hidden = false;
    }
    if (thumbBar) {
      thumbBar.hidden = true;
    }
  }

  function resetThumbPanel() {
    thumbRequestToken += 1; // 進行中のリクエストの結果を無効化する
    thumbLoadedPages = new Set();
    thumbSelectedPages = new Set();
    thumbLoadedMin = null;
    thumbLoadedMax = null;
    thumbAnchorPage = 1;
    thumbLoadingDirection = null;
    resetThumbDetail();
    if (thumbGrid) {
      thumbGrid.innerHTML = '';
      delete thumbGrid.dataset.initialized;
    }
    if (thumbHeading) {
      thumbHeading.textContent = '';
    }
    exitThumbMode();
    setThumbStatus('');
    updateThumbApplyButton();
    updateThumbRangeButtons();
  }

  function insertThumbCell(pageNumber, base64Data) {
    const cell = document.createElement('div');
    cell.className = 'modal-thumb-item';
    cell.dataset.page = String(pageNumber);
    if (thumbSelectedPages.has(pageNumber)) {
      cell.classList.add('selected');
    }
    if (pageNumber === thumbAnchorPage) {
      cell.classList.add('current-page');
    }
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'modal-thumb-checkbox';
    checkbox.checked = thumbSelectedPages.has(pageNumber);
    checkbox.id = `pdf-modal-thumb-check-${pageNumber}`;
    const img = document.createElement('img');
    img.src = `data:image/jpeg;base64,${base64Data}`;
    img.loading = 'lazy';
    img.alt = `p.${pageNumber}`;
    img.title = 'ダブルクリックで拡大';
    const checkControl = document.createElement('label');
    checkControl.className = 'modal-thumb-check-control';
    checkControl.setAttribute('for', checkbox.id);
    checkControl.setAttribute('aria-label', `p.${pageNumber} を選択`);
    const badge = document.createElement('span');
    badge.className = 'modal-thumb-check-badge';
    badge.textContent = '✓';
    badge.setAttribute('aria-hidden', 'true');
    const pageLabel = document.createElement('span');
    pageLabel.className = 'modal-thumb-page-label';
    pageLabel.textContent = pageNumber === thumbAnchorPage ? `p.${pageNumber}（現在）` : `p.${pageNumber}`;
    const zoomButton = document.createElement('button');
    zoomButton.type = 'button';
    zoomButton.className = 'modal-thumb-zoom-button';
    zoomButton.textContent = '⌕';
    zoomButton.title = '拡大プレビュー';
    zoomButton.setAttribute('aria-label', `p.${pageNumber} を拡大プレビュー`);
    checkControl.appendChild(checkbox);
    checkControl.appendChild(badge);
    cell.appendChild(checkControl);
    cell.appendChild(img);
    cell.appendChild(pageLabel);
    cell.appendChild(zoomButton);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) {
        thumbSelectedPages.add(pageNumber);
        cell.classList.add('selected');
      } else {
        thumbSelectedPages.delete(pageNumber);
        cell.classList.remove('selected');
      }
      updateThumbApplyButton();
    });
    zoomButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      void loadThumbDetail(pageNumber, { returnFocus: zoomButton });
    });
    img.addEventListener('dblclick', (event) => {
      event.preventDefault();
      event.stopPropagation();
      void loadThumbDetail(pageNumber, { returnFocus: zoomButton });
    });

    // レスポンスの到着順はページ番号順と限らないため、挿入位置をページ番号で揃える
    const target = [...thumbGrid.children].find((child) => Number(child.dataset.page) > pageNumber);
    if (target) {
      thumbGrid.insertBefore(cell, target);
    } else {
      thumbGrid.appendChild(cell);
    }
  }

  // 成功可否を返す。呼び出し側は成否に応じて thumbLoadedMin/Max を扱う
  async function loadThumbRange(start, end) {
    if (!currentPdfPath || start > end) {
      return false;
    }
    const clampedStart = Math.max(1, start);
    const clampedEnd = currentPageCount ? Math.min(end, currentPageCount) : end;
    if (clampedEnd < clampedStart) {
      return false;
    }
    const token = thumbRequestToken;
    setThumbStatus('サムネイルを読み込み中...');
    try {
      const response = await fetch(
        `/pdf-thumbnails?${new URLSearchParams({
          pdf_path: currentPdfPath,
          pages: `${clampedStart}-${clampedEnd}`,
          size: 'thumbnail',
        })}`
      );
      if (token !== thumbRequestToken) {
        return false;
      }
      if (!response.ok) {
        setThumbStatus('サムネイルの取得に失敗しました', true);
        return false;
      }
      const payload = await response.json();
      if (token !== thumbRequestToken) {
        return false;
      }
      for (const item of payload.pages) {
        if (thumbLoadedPages.has(item.page)) {
          continue;
        }
        thumbLoadedPages.add(item.page);
        insertThumbCell(item.page, item.data);
      }
      thumbLoadedMin = thumbLoadedMin === null ? clampedStart : Math.min(thumbLoadedMin, clampedStart);
      thumbLoadedMax = thumbLoadedMax === null ? clampedEnd : Math.max(thumbLoadedMax, clampedEnd);
      setThumbStatus('');
      return true;
    } catch (error) {
      if (token === thumbRequestToken) {
        setThumbStatus('サムネイルの取得に失敗しました', true);
      }
      console.warn('Failed to load thumbnails', error);
      return false;
    }
  }

  // 既にロード済みの範囲を前後に広げる（置き換えではなく追加）
  async function expandThumbRange(direction) {
    if (thumbLoadingDirection || thumbLoadedMin === null || thumbLoadedMax === null) {
      return;
    }
    thumbLoadingDirection = direction;
    updateThumbRangeButtons();
    if (direction === 'prev') {
      const newStart = Math.max(1, thumbLoadedMin - THUMB_EXPAND_STEP);
      if (newStart < thumbLoadedMin) {
        await loadThumbRange(newStart, thumbLoadedMin - 1);
      }
    } else {
      const newEnd = currentPageCount
        ? Math.min(currentPageCount, thumbLoadedMax + THUMB_EXPAND_STEP)
        : thumbLoadedMax + THUMB_EXPAND_STEP;
      if (newEnd > thumbLoadedMax) {
        await loadThumbRange(thumbLoadedMax + 1, newEnd);
      }
    }
    thumbLoadingDirection = null;
    updateThumbRangeButtons();
  }

  async function ensureThumbPanelInitialized() {
    if (!thumbGrid || thumbGrid.dataset.initialized === '1') {
      return;
    }
    thumbGrid.dataset.initialized = '1';
    // cart の初回ロードが未完了だと「資料内の本」を誤って「新規」と
    // 判定してしまう（空キャッシュのため）。判定前に必ず完了を待つ
    await TsundokuCart.ready;

    // 初期チェック状態: 既にこの本が資料に入っていればそのページを、
    // なければモーダルのページ欄（検索起点等）の現在値を起点にする
    const entry = currentCartEntry();
    const baseSpec = entry ? entry.pages : pagesInput.value;
    for (const interval of TsundokuPages.specToIntervals(baseSpec || '')) {
      if (interval.open) {
        continue; // 開区間はサムネイル一覧（有限枚数）では表現しない
      }
      for (let page = interval.start; page <= interval.end; page += 1) {
        thumbSelectedPages.add(page);
      }
    }
    updateThumbApplyButton();

    // 起点ページ: ページ欄（検索結果起点ならヒットページ） → URL の #page= →
    // 資料内の既存ページ → 1 の優先順位。画面に見えているページ欄を最優先する。
    // URL の #page= は検索結果グループ化ロジックが選んだ代表ページであり、
    // ページ欄に見えているヒット一覧（利用者が実際に見ている文脈）とズレることが
    // あるため、ページ欄より後ろにする。workspace起点はページ欄が空なので
    // 従来どおり URL → entry.pages の順にフォールバックする
    thumbAnchorPage =
      firstPageOfSpec(pagesInput.value) ||
      extractPageFromUrl(frame.src) ||
      firstPageOfSpec(entry ? entry.pages : '') ||
      1;
    if (thumbHeading) {
      thumbHeading.textContent = `p.${thumbAnchorPage} 周辺のページを選ぶ`;
    }
    const start = Math.max(1, thumbAnchorPage - THUMB_INITIAL_RADIUS);
    const end = thumbAnchorPage + THUMB_INITIAL_RADIUS;
    await loadThumbRange(start, end);
    updateThumbRangeButtons();
  }

  thumbToggleButton?.addEventListener('click', () => {
    enterThumbMode();
  });

  thumbBackButton?.addEventListener('click', () => {
    exitThumbMode();
  });

  thumbPrevButton?.addEventListener('click', () => {
    void expandThumbRange('prev');
  });

  thumbNextButton?.addEventListener('click', () => {
    void expandThumbRange('next');
  });

  thumbDetailCloseButton?.addEventListener('click', () => {
    closeThumbDetailOverlay();
  });

  thumbDetailPrevButton?.addEventListener('click', () => {
    moveThumbDetail(-1);
  });

  thumbDetailNextButton?.addEventListener('click', () => {
    moveThumbDetail(1);
  });

  thumbDetailOverlay?.addEventListener('click', (event) => {
    if (event.target === thumbDetailOverlay) {
      closeThumbDetailOverlay();
    }
  });

  document.addEventListener('keydown', handleThumbDetailKeydown, true);

  thumbApplyButton?.addEventListener('click', async () => {
    // cart初回ロード未完了だと資料内の本を誤って「新規」判定してしまうため待つ
    await TsundokuCart.ready;
    const sortedPages = [...thumbSelectedPages].sort((a, b) => a - b);
    const newSpec = TsundokuPages.pagesToSpec(sortedPages);
    // ページ欄は常に同期する。資料内の本の場合にここを省略すると、反映後に
    // 「資料に追加」を押した際、古いページ欄の値がマージされて反映前の
    // 状態が復活してしまう（追加＝マージ、反映＝置き換え、の役割分担が壊れる）
    pagesInput.value = newSpec;
    pagesInput.dispatchEvent(new Event('input'));
    // save() は load() で得た同一の cart オブジェクトをミューテートしてから
    // 渡す必要があるため、判定用の currentCartEntry() ではなくここで直接取得する
    const cart = TsundokuCart.load();
    if (!Array.isArray(cart.items)) {
      cart.items = [];
    }
    const entry = currentPackItemKey
      ? cart.items.find((item) => TsundokuCart.itemKey(item) === currentPackItemKey)
      : null;
    if (entry) {
      // 資料棚起点（既にこの本が資料内にある）: そのページ範囲を直接置き換えて即保存
      entry.pages = newSpec;
      TsundokuCart.save(cart);
      setThumbStatus(`${sortedPages.length}ページを資料に反映しました`);
      document.dispatchEvent(new CustomEvent('tsundoku-cart-updated'));
    } else {
      // 検索結果起点（まだ資料にない）: ページ欄の反映のみ。「資料に追加」で確定する
      setThumbStatus(`${sortedPages.length}ページを選択しました`);
    }
  });

  function clearChapterSelection() {
    if (chaptersMenu) {
      for (const input of chaptersMenu.querySelectorAll('input:checked')) {
        input.checked = false;
      }
    }
    pagesInput.value = '';
    updateChaptersSummary(0);
    setExportLink();
  }

  function updateChaptersSummary(selectedCount) {
    if (chaptersSummary) {
      chaptersSummary.textContent = selectedCount > 0 ? `章 ${selectedCount}件選択` : '章を選択';
    }
  }

  function resetChapters() {
    if (!chaptersWrap || !chaptersMenu) {
      return;
    }
    chaptersWrap.hidden = true;
    chaptersWrap.removeAttribute('open');
    chaptersMenu.innerHTML = '';
    updateChaptersSummary(0);
  }

  async function loadChapters(pdfPath) {
    resetChapters();
    currentPageCount = null;
    if (!chaptersWrap || !chaptersMenu || !pdfPath) {
      return;
    }
    const token = ++chaptersRequestToken;
    let chapters = [];
    let pageCount = null;
    try {
      const response = await fetch(`/pdf-outline?${new URLSearchParams({ pdf_path: pdfPath })}`);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      chapters = Array.isArray(payload.chapters) ? payload.chapters : [];
      pageCount = Number.isInteger(payload.page_count) && payload.page_count > 0 ? payload.page_count : null;
    } catch (error) {
      console.warn('Failed to load PDF outline', error);
      return;
    }
    if (token !== chaptersRequestToken) {
      return;
    }
    currentPageCount = pageCount;
    setExportLink();
    if (chapters.length === 0) {
      return;
    }
    const clearButton = document.createElement('button');
    clearButton.type = 'button';
    clearButton.className = 'modal-chapters-clear';
    clearButton.textContent = '選択をクリア';
    clearButton.addEventListener('click', clearChapterSelection);
    chaptersMenu.appendChild(clearButton);
    for (const chapter of chapters) {
      const item = document.createElement('label');
      item.className = 'modal-chapter-item';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = chapter.pages;
      const text = document.createElement('span');
      const indent = '　'.repeat(Math.max(0, (chapter.level || 1) - 1));
      const range = chapter.start_page === chapter.end_page
        ? `p.${chapter.start_page}`
        : `p.${chapter.start_page}-${chapter.end_page}`;
      text.textContent = `${indent}${chapter.title} (${range})`;
      item.appendChild(checkbox);
      item.appendChild(text);
      chaptersMenu.appendChild(item);
    }
    chaptersWrap.hidden = false;
  }

  function setExportStatus(message, isError = false) {
    if (exportStatus) {
      exportStatus.textContent = message || '';
      exportStatus.classList.toggle('is-error', Boolean(isError && message));
    }
  }

  function exportPdfUrl() {
    if (!currentPdfPath || !pagesInput.value.trim()) {
      return '';
    }
    const params = new URLSearchParams({
      pdf_path: currentPdfPath,
      pages: pagesInput.value.trim(),
    });
    return `/export-pdf?${params.toString()}`;
  }

  function filenameFromContentDisposition(header) {
    const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(header || '');
    if (utf8Match) {
      try {
        return decodeURIComponent(utf8Match[1]);
      } catch (error) {
        console.warn('Failed to decode filename', error);
      }
    }
    const asciiMatch = /filename="?([^";]+)"?/i.exec(header || '');
    return asciiMatch ? asciiMatch[1] : 'export.pdf';
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || 'export.pdf';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function fetchExportedPdfBlob() {
    const url = exportPdfUrl();
    if (!url) {
      throw new Error('ページを指定してください');
    }
    const response = await fetch(url, { headers: { 'Accept': 'application/pdf' } });
    if (!response.ok) {
      const message = await response.text().catch(() => '');
      throw new Error(message || 'PDFの作成に失敗しました');
    }
    return {
      blob: await response.blob(),
      filename: filenameFromContentDisposition(response.headers.get('Content-Disposition')),
    };
  }

  async function shareExportedPdf() {
    if (!canUseWebShare()) {
      return;
    }
    try {
      setExportStatus('指定ページPDFを作成中...');
      const { blob, filename } = await fetchExportedPdfBlob();
      const file = new File([blob], filename, { type: 'application/pdf' });
      if (typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] })) {
        await navigator.share({
          title: title.textContent || 'PDF',
          files: [file],
        });
        setExportStatus('');
        return;
      }
      downloadBlob(blob, filename);
      setExportStatus('ファイル共有に対応していないため、ダウンロードしました。');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setExportStatus('');
        return;
      }
      setExportStatus(error instanceof Error ? error.message : '共有に失敗しました', true);
    } finally {
      shareDetails?.removeAttribute('open');
    }
  }

  function openModal(url, label, scrapboxUrl, pdfPath, pages, itemKey = null) {
    resetThumbPanel();
    frame.src = url;
    openLink.href = url;
    title.textContent = label || 'PDF';
    setScrapboxLink(scrapboxUrl);
    currentPdfPath = pdfPath || '';
    currentPackItemKey = typeof itemKey === 'string' && itemKey ? itemKey : null;
    pagesInput.value = pages || '';
    if (addWorkspaceButton) {
      // 検索・資料棚どちらの起点でも、PDFパスが分かるモーダルなら資料へ追加できる
      addWorkspaceButton.hidden = !currentPdfPath;
    }
    if (thumbToggleButton) {
      thumbToggleButton.hidden = !currentPdfPath;
    }
    setExportLink();
    void loadChapters(currentPdfPath);
    renderShareMenu();
    modal.querySelector('.modal-share').removeAttribute('open');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    updateFullscreenButton();
    updateAddWorkspaceButtonLabel();
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-pdf-modal-url]');
    if (!trigger) {
      return;
    }
    event.preventDefault();
    openModal(
      trigger.dataset.pdfModalUrl,
      trigger.dataset.pdfModalTitle,
      trigger.dataset.pdfModalScrapboxUrl,
      trigger.dataset.pdfModalPath,
      trigger.dataset.pdfModalPages,
      trigger.dataset.pdfModalItemKey || null,
    );
  });

  function shortPackName(name) {
    return name.length > 10 ? `${name.slice(0, 10)}…` : name;
  }

  function updateAddWorkspaceButtonLabel() {
    if (!addWorkspaceButton) {
      return;
    }
    const pack = TsundokuCart.getActivePack();
    if (currentPackItemKey) {
      addWorkspaceButton.textContent = 'ページ範囲を更新';
    } else {
      addWorkspaceButton.textContent = pack ? `「${shortPackName(pack.name)}」に追加` : '資料に追加';
    }
  }

  document.addEventListener('tsundoku-cart-updated', updateAddWorkspaceButtonLabel);
  updateAddWorkspaceButtonLabel();

  addWorkspaceButton?.addEventListener('click', async () => {
    const pages = pagesInput.value.trim();
    if (!currentPdfPath || !pages) {
      return;
    }
    const error = TsundokuPages.validatePageSpec(pages, currentPageCount);
    if (error) {
      setExportStatus(error, true);
      return;
    }
    if (!TsundokuCart.getActivePack()) {
      // 資料がまだない。作成してから追加する
      const created = await TsundokuCart.ensureActivePackInteractive();
      if (!created) {
        return;
      }
    }
    const cart = TsundokuCart.load();
    if (!Array.isArray(cart.items)) {
      cart.items = [];
    }
    const entry = currentPackItemKey
      ? cart.items.find((item) => TsundokuCart.itemKey(item) === currentPackItemKey)
      : null;
    if (entry) {
      entry.pages = pages;
    } else {
      cart.items.push({
        clientId: TsundokuCart.createClientId(),
        pdf_path: currentPdfPath,
        title: title.textContent || currentPdfPath,
        pages: pages,
        collapsed: false,
        addedAt: new Date().toISOString(),
      });
    }
    TsundokuCart.save(cart);
    const pack = TsundokuCart.getActivePack();
    const packName = pack ? pack.name : '資料';
    const displayPages = pages ? `p.${pages}` : '全ページ';
    const bookTitle = title.textContent || currentPdfPath;
    if (entry) {
      setExportStatus(`「${shortPackName(packName)}」の「${bookTitle}」のページ範囲を ${displayPages} に更新しました`);
    } else {
      setExportStatus(`「${shortPackName(packName)}」に「${bookTitle}」の ${displayPages} を追加しました`);
    }
    document.dispatchEvent(new CustomEvent('tsundoku-cart-updated'));
  });

  pagesInput.addEventListener('input', setExportLink);

  chaptersMenu?.addEventListener('change', () => {
    const selected = [...chaptersMenu.querySelectorAll('input:checked')].map((input) => input.value);
    pagesInput.value = TsundokuPages.mergeSpecs(selected);
    updateChaptersSummary(selected.length);
    setExportLink();
  });

  fullscreenButton?.addEventListener('click', () => {
    void toggleFullscreen();
  });
  document.addEventListener('fullscreenchange', updateFullscreenButton);
  updateFullscreenButton();

  closeButton.addEventListener('click', () => {
    void closeModal();
  });
  modal.addEventListener('click', (event) => {
    if (event.target === modal) {
      void closeModal();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (isThumbDetailOverlayOpen()) {
      return;
    }
    if (event.key === 'Escape' && modal.classList.contains('open')) {
      void closeModal();
    }
  });
})();
