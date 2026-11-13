
import { cached, fetchJson } from '../cache.js';

/*
 * DayPane 综合新闻模块
 *
 * 中文版：
 * 社会、国际、财经、科技，各显示一条。
 * 优先最近 24 小时，最多回溯 48 小时。
 *
 * 英文版：
 * 保留原有 Hacker News 逻辑，确保英文看板兼容。
 *
 * 输出接口保持：
 * { ok, items, source, error }
 */

const CACHE_MS = 10 * 60 * 1000;
const MAX_AGE_MS = 48 * 60 * 60 * 1000;
const PREFERRED_AGE_MS = 24 * 60 * 60 * 1000;
const FETCH_TIMEOUT_MS = 8000;

// ===== 新闻来源 =====

const NEWS_GROUPS = [
  {
    category: '社会',
    feeds: [
      {
        name: '中国新闻网',
        url: 'https://www.chinanews.com.cn/rss/society.xml'
      }
    ]
  },
  {
    category: '国际',
    feeds: [
      {
        name: '中国新闻网',
        url: 'https://www.chinanews.com.cn/rss/world.xml'
      }
    ]
  },
  {
    category: '财经',
    feeds: [
      {
        name: '中国新闻网',
        url: 'https://www.chinanews.com.cn/rss/finance.xml'
      },
      {
        name: 'FT中文网',
        url: 'https://www.ftchinese.com/rss/feed'
      }
    ]
  },
  {
    category: '科技',
    feeds: [
      {
        name: 'Solidot',
        url: 'https://www.solidot.org/index.rss'
      },
      {
        name: '量子位',
        url: 'https://www.qbitai.com/feed'
      }
    ]
  }
];

// ===== 基础筛选 =====

// 仅过滤明显推广内容，不使用 AI 专属关键词。
const PROMO_RE =
  /招聘启事|招聘公告|征稿启事|投稿须知|优惠券|限时优惠|培训招生|广告合作|商务合作|诚邀报名|课程促销/;

// ===== XML 文本处理 =====

function decodeXml(value) {
  if (!value) return '';

  let text = String(value)
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/<[^>]*>/g, '');

  const entities = {
    amp: '&',
    lt: '<',
    gt: '>',
    quot: '"',
    apos: "'",
    nbsp: ' '
  };

  text = text.replace(
    /&(#x[0-9a-f]+|#[0-9]+|[a-z]+);/gi,
    (match, entity) => {
      if (entity.startsWith('#')) {
        const hex = /^#x/i.test(entity);
        const num = parseInt(
          entity.slice(hex ? 2 : 1),
          hex ? 16 : 10
        );

        if (
          Number.isInteger(num) &&
          num >= 0 &&
          num <= 0x10ffff
        ) {
          return String.fromCodePoint(num);
        }

        return match;
      }

      return entities[entity.toLowerCase()] ?? match;
    }
  );

  return text.replace(/\s+/g, ' ').trim();
}

function readTag(block, tag) {
  const pattern = new RegExp(
    `<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`,
    'i'
  );

  const match = block.match(pattern);
  return match ? decodeXml(match[1]) : '';
}

// ===== RSS 解析 =====

function parseFeed(xml, source, limit = 40) {
  const blocks =
    xml.match(/<item\b[^>]*>[\s\S]*?<\/item>/gi) ||
    xml.match(/<entry\b[^>]*>[\s\S]*?<\/entry>/gi) ||
    [];

  const items = [];

  for (const block of blocks.slice(0, limit)) {
    const title = readTag(block, 'title');

    if (!title || title.length < 6) continue;
    if (PROMO_RE.test(title)) continue;

    const dateText =
      readTag(block, 'pubDate') ||
      readTag(block, 'dc:date') ||
      readTag(block, 'published') ||
      readTag(block, 'updated');

    const timestamp = Date.parse(dateText);

    const publishedAt = Number.isFinite(timestamp)
      ? timestamp
      : null;

    const url = readTag(block, 'link');

    items.push({
      title,
      source,
      url,
      publishedAt
    });
  }

  return items;
}

// ===== 网络获取 =====

async function fetchRss(url, source) {
  const controller = new AbortController();

  const timer = setTimeout(
    () => controller.abort(),
    FETCH_TIMEOUT_MS
  );

  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; DayPane/1.0)',
        Accept: 'application/rss+xml, application/xml, text/xml'
      }
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const xml = await response.text();
    const items = parseFeed(xml, source);

    if (!items.length) {
      throw new Error('RSS 中没有有效新闻');
    }

    return items;
  } finally {
    clearTimeout(timer);
  }
}

// ===== 去重 =====

function normalizeTitle(title) {
  return title
    .toLowerCase()
    .replace(/[\s\p{P}\p{S}]/gu, '');
}

// ===== 每类选择一条新闻 =====

async function collectGroup(group, now) {
  const results = await Promise.all(
    group.feeds.map(async feed => {
      try {
        const items = await fetchRss(feed.url, feed.name);

        return {
          ok: true,
          items
        };
      } catch (error) {
        return {
          ok: false,
          items: [],
          error: `${feed.name}: ${error.message}`
        };
      }
    })
  );

  const allItems = results.flatMap(result => result.items);

  // 无日期或超过 48 小时的报道不当成今日新闻。
  const recent = allItems.filter(item => {
    if (item.publishedAt === null) return false;

    const age = now - item.publishedAt;

    return age >= -5 * 60 * 1000 && age <= MAX_AGE_MS;
  });

  // 去重，并按发布时间降序。
  const seen = new Set();

  const unique = recent
    .sort((a, b) => b.publishedAt - a.publishedAt)
    .filter(item => {
      const key = normalizeTitle(item.title);

      if (seen.has(key)) return false;

      seen.add(key);
      return true;
    });

  // 优先从最近 24 小时选择。
  const preferred = unique.filter(
    item => now - item.publishedAt <= PREFERRED_AGE_MS
  );

  const selected = preferred[0] || unique[0] || null;

  const errors = results
    .filter(result => !result.ok)
    .map(result => result.error);

  return {
    category: group.category,
    selected,
    available: results.some(result => result.ok),
    errors
  };
}

// ===== 中文综合新闻 =====

async function getChineseNews() {
  const now = Date.now();

  const groups = await Promise.all(
    NEWS_GROUPS.map(group => collectGroup(group, now))
  );

  const items = [];
  const errors = [];
  let realCount = 0;

  for (const group of groups) {
    if (group.selected) {
      realCount++;

      items.push({
        title: `【${group.category}】${group.selected.title}`,
        category: group.category,
        source: group.selected.source,
        url: group.selected.url,
        publishedAt: group.selected.publishedAt
      });
    } else {
      // 缺少有效报道时明确提示，不虚构新闻。
      const message = group.available
        ? '近48小时暂无可用新闻'
        : '新闻源暂不可用';

      items.push({
        title: `【${group.category}】${message}`,
        category: group.category,
        source: 'DayPane',
        url: '',
        publishedAt: null,
        unavailable: true
      });
    }

    errors.push(...group.errors);
  }

  return {
    ok: realCount > 0,
    items,
    source: '综合 RSS',
    error: errors.join('; ') || null
  };
}

// ===== 英文版：保留原有兼容逻辑 =====

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

// ===== 对外接口：保持 DayPane 原版格式 =====

export function getNews(language = 'zh') {
  if (language === 'en') {
    return cached(
      'news-en',
      CACHE_MS,
      getEnglishNews
    );
  }

  return cached(
    'news',
    CACHE_MS,
    getChineseNews
  );
}
