import { cached, fetchJson } from '../cache.js';

const CACHE_MS = 10 * 60 * 1000;
const REBANG = 'https://rebang.today/?tab=top';
const AGGREGATE = 'https://tunbang.com/hot/';
const BAIDU = 'https://top.baidu.com/board?tab=realtime';

async function getBaidu() {
  const source = '百度热搜';
  try {
    const response = await fetch(BAIDU, { signal: AbortSignal.timeout(8000) });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const html = await response.text();
    const payload = html.match(/<!--s-data:([\s\S]*?)-->/)?.[1];
    const ranked = JSON.parse(payload)?.data?.cards?.find(card => card.component === 'rankList')?.content;
    if (!Array.isArray(ranked)) throw new Error('Rank list missing');
    const seen = new Set();
    const items = [];
    for (const item of ranked) {
      const title = typeof item?.word === 'string' ? item.word.trim().replace(/\s+/g, ' ') : '';
      if (!title || item.isTop || seen.has(title)) continue;
      seen.add(title);
      items.push({ title, source, url: 'https://www.baidu.com/s?wd=' + encodeURIComponent(title) });
      if (items.length === 8) break;
    }
    return { ok: items.length > 0, items, source };
  } catch (error) {
    return { ok: false, items: [], source, error: error.message };
  }
}

function decodeHtml(text) {
  const entities = { amp: '&', quot: '"', apos: "'", lt: '<', gt: '>', nbsp: ' ' };
  return text.replace(/&(#(?:x[0-9a-f]+|[0-9]+)|[a-z]+);/gi, (original, token) => {
    if (!token.startsWith('#')) return entities[token] ?? original;
    const point = token[1].toLowerCase() === 'x' ? parseInt(token.slice(2), 16) : Number(token.slice(1));
    return point > 0 && point <= 0x10ffff ? String.fromCodePoint(point) : original;
  });
}

async function getTunbang() {
  try {
    const response = await fetch(AGGREGATE, { signal: AbortSignal.timeout(8000) });
    if (response.ok) {
      const html = await response.text();
      const list = html.match(/<ol\b[^>]*\bid="list"[^>]*>([\s\S]*?)<\/ol>/i)?.[1] ?? '';
      const update = html.match(/数据更新于\s*(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/);
      const updatedAt = update ? Date.parse(update[1] + 'T' + update[2] + ':00+08:00') : NaN;
      if (!Number.isFinite(updatedAt) || Math.abs(Date.now() - updatedAt) > 2 * 3600000) return getBaidu();
      const seen = new Set();
      const items = [];
      const source = '豚榜';
      for (const [, link, markup] of list.matchAll(/<a\b[^>]*class="row-title"[^>]*href="(https:[^"]+)"[^>]*>([\s\S]*?)<\/a>/gi)) {
        const title = decodeHtml(markup.replace(/<[^>]*>/g, '')).trim().replace(/\s+/g, ' ');
        const key = title.normalize('NFKC').replace(/[\s\p{P}]+/gu, '');
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push({ title, source, url: decodeHtml(link) });
        if (items.length === 8) break;
      }
      if (items.length) return { ok: true, items, source, updatedAt };
    }
  } catch {
    // The official single-platform list keeps the display usable if the aggregator is unavailable.
  }
  return getBaidu();
}

async function getChineseNews() {
  try {
    const response = await fetch(REBANG, { signal: AbortSignal.timeout(8000) });
    if (response.ok) {
      const source = '今日热榜';
      const html = await response.text();
      const seen = new Set();
      const items = [];
      for (const [, link, markup] of html.matchAll(/<a\b[^>]*\bdata-item-title-link="true"[^>]*\bhref="(https:[^"]+)"[^>]*>([\s\S]*?)<\/a>/gi)) {
        const title = decodeHtml(markup.replace(/<[^>]*>/g, '')).trim().replace(/\s+/g, ' ');
        const url = decodeHtml(link);
        if (!title || seen.has(url)) continue;
        seen.add(url);
        items.push({ title, source, url });
        if (items.length === 8) break;
      }
      if (items.length) return { ok: true, items, source };
    }
  } catch {
    // Keep the dashboard usable during a Rebang outage.
  }
  return getTunbang();
}

async function getEnglishNews() {
  const response = await fetchJson(
    'https://hn.algolia.com/api/v1/search_by_date?query=AI&tags=story&hitsPerPage=12'
  );

  const items = (response.data?.hits || [])
    .filter(hit => hit.title)
    .slice(0, 6)
    .map(hit => ({
      title: hit.title,
      source: 'HN',
      url: hit.url
    }));

  return {
    ok: response.ok && items.length > 0,
    items,
    source: 'HN',
    error: response.error
  };
}

export function getNews(language = 'zh') {
  return language === 'en'
    ? cached('news-en', CACHE_MS, getEnglishNews)
    : cached('news', CACHE_MS, getChineseNews);
}
