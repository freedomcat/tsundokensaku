// ページ指定 spec（例: "3-7,20-35" / 開区間 "39-" "-10"）の共有ユーティリティ。
// モーダル・検索ページ・ワークスペースで共通利用する。
window.TsundokuPages = (() => {
  function parseChunk(chunk) {
    const text = String(chunk || '').trim();
    if (!text) {
      return null;
    }
    const match = /^(\d*)-(\d*)$|^(\d+)$/.exec(text);
    if (!match || (match[3] === undefined && !match[1] && !match[2])) {
      return null;
    }
    if (match[3] !== undefined) {
      const page = parseInt(match[3], 10);
      return { start: page, end: page, open: false, text };
    }
    const start = match[1] ? parseInt(match[1], 10) : null;
    const end = match[2] ? parseInt(match[2], 10) : null;
    return { start, end, open: start === null || end === null, text };
  }

  function chunksOf(spec) {
    return String(spec || '')
      .split(',')
      .map((chunk) => chunk.trim())
      .filter((chunk) => chunk !== '');
  }

  function validatePageSpec(spec, pageCount) {
    let validChunks = 0;
    for (const raw of chunksOf(spec)) {
      const parsed = parseChunk(raw);
      if (!parsed) {
        return `ページ指定が不正です: ${raw}`;
      }
      const start = parsed.start === null ? 1 : parsed.start;
      const end = parsed.end;
      if (start < 1 || (end !== null && end < 1)) {
        return `ページ番号は1以上で指定してください: ${raw}`;
      }
      if (end !== null && start > end) {
        return `範囲の始まりが終わりより大きいです: ${raw}`;
      }
      if (pageCount !== null && pageCount !== undefined) {
        const upper = end === null ? start : end;
        if (start > pageCount || upper > pageCount) {
          return `ページ番号が範囲外です: ${raw}（1-${pageCount}）`;
        }
      }
      validChunks += 1;
    }
    if (validChunks === 0) {
      return 'ページを指定してください';
    }
    return '';
  }

  function compressIntervals(intervals) {
    const sorted = [...intervals].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const merged = [];
    for (const [start, end] of sorted) {
      const last = merged[merged.length - 1];
      if (last && start <= last[1] + 1) {
        last[1] = Math.max(last[1], end);
      } else {
        merged.push([start, end]);
      }
    }
    return merged;
  }

  function intervalsToSpec(intervals) {
    return intervals
      .map(([start, end]) => (start === end ? String(start) : `${start}-${end}`))
      .join(',');
  }

  // 閉区間はソート・結合して正規化。開区間や解釈できない部分は末尾へそのまま残す。
  function mergeSpecs(specs) {
    const intervals = [];
    const passthrough = [];
    for (const spec of specs) {
      for (const raw of chunksOf(spec)) {
        const parsed = parseChunk(raw);
        if (parsed && !parsed.open) {
          intervals.push([Math.min(parsed.start, parsed.end), Math.max(parsed.start, parsed.end)]);
        } else if (!passthrough.includes(raw)) {
          passthrough.push(raw);
        }
      }
    }
    const pieces = [];
    const closedSpec = intervalsToSpec(compressIntervals(intervals));
    if (closedSpec) {
      pieces.push(closedSpec);
    }
    pieces.push(...passthrough);
    return pieces.join(',');
  }

  function pagesToSpec(pageNumbers) {
    const intervals = [...new Set(pageNumbers)]
      .filter((page) => Number.isInteger(page) && page >= 1)
      .map((page) => [page, page]);
    return intervalsToSpec(compressIntervals(intervals));
  }

  function specContainsPages(spec, pageNumbers) {
    const chunks = chunksOf(spec)
      .map((raw) => parseChunk(raw))
      .filter((parsed) => parsed !== null);
    return pageNumbers.every((page) =>
      chunks.some((parsed) => {
        const start = parsed.start === null ? 1 : parsed.start;
        const end = parsed.end === null ? Infinity : parsed.end;
        return page >= start && page <= end;
      })
    );
  }

  // 閉区間から指定ページを取り除く。開区間・不正チャンクはそのまま残す。
  function subtractPages(spec, pageNumbers) {
    const removal = new Set(pageNumbers);
    const pieces = [];
    for (const raw of chunksOf(spec)) {
      const parsed = parseChunk(raw);
      if (!parsed || parsed.open) {
        pieces.push(raw);
        continue;
      }
      const kept = [];
      let rangeStart = null;
      for (let page = parsed.start; page <= parsed.end; page += 1) {
        if (removal.has(page)) {
          if (rangeStart !== null) {
            kept.push([rangeStart, page - 1]);
            rangeStart = null;
          }
        } else if (rangeStart === null) {
          rangeStart = page;
        }
      }
      if (rangeStart !== null) {
        kept.push([rangeStart, parsed.end]);
      }
      const keptSpec = intervalsToSpec(kept);
      if (keptSpec) {
        pieces.push(keptSpec);
      }
    }
    return pieces.join(',');
  }

  // spec文字列を表示用の区間リストに変換する。チップ表示用。
  // 例: "3-7,20,39-" -> [{start:3,end:7,open:false,text:"3-7"}, ...]
  // text は元のチャンク文字列（開区間を丸ごと除去したいときに使う）。
  // 解釈できないチャンクは無視する。
  function specToIntervals(spec) {
    const intervals = [];
    for (const raw of chunksOf(spec)) {
      const parsed = parseChunk(raw);
      if (!parsed) {
        continue;
      }
      intervals.push({ start: parsed.start, end: parsed.end, open: parsed.open, text: parsed.text });
    }
    return intervals;
  }

  // 閉区間のみのspecの総ページ数。開区間を含む場合は null。
  function countPages(spec) {
    const intervals = [];
    for (const raw of chunksOf(spec)) {
      const parsed = parseChunk(raw);
      if (!parsed) {
        continue;
      }
      if (parsed.open) {
        return null;
      }
      intervals.push([parsed.start, parsed.end]);
    }
    return compressIntervals(intervals).reduce((total, [start, end]) => total + (end - start + 1), 0);
  }

  return {
    validatePageSpec,
    mergeSpecs,
    pagesToSpec,
    specContainsPages,
    subtractPages,
    countPages,
    specToIntervals,
  };
})();
