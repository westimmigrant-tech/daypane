
import { cached, fetchJson } from '../cache.js';

/*
 * DayPane 综合新闻模块
 *
 * 中文版：
 * 国际、国内、AI、影视，各最多两条，先覆盖四个栏目。
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
const FETCH_TIMEOUT_MS = 8000;
const HOUR_MS = 60 * 60 * 1000;
const CHINANEWS = 'https://www.chinanews.com.cn/rss/';

// ===== 新闻来源 =====

const chinaNews = (channel) => ({ name: '中国新闻网', url: CHINANEWS + channel + '.xml' });
function searchNews(query) {
  const params = new URLSearchParams({
    q: query + ' when:2d', hl: 'zh-CN', gl: 'CN', ceid: 'CN:zh-Hans'
  });
  return { name: 'Google 新闻', url: 'https://news.google.com/rss/search?' + params, aggregator: true };
}

const NEWS_GROUPS = [
  {
    category: '国际',
    feeds: [chinaNews('world')],
    major: /停火|和平协议|袭击|伤亡|地震|海啸|大选|选举结果|政权|国会|关税|制裁|利率|央行|峰会|国防预算|防卫费|停飞令|停摆/
  },
  {
    category: '国内',
    feeds: [chinaNews('china'), chinaNews('society'), chinaNews('finance')],
    major: /国务院|全国人大|央行|降准|降息|国家统计局|国家标准|医保|全国.{0,12}(实施|发布)|地震|台风|洪灾|APEC.{0,12}(开幕|闭幕|会晤|声明)|会议.{0,8}筹备.{0,4}就绪/i
  },
  {
    category: 'AI',
    feeds: [
      searchNews('(人工智能 OR 大模型 OR OpenAI OR Anthropic OR Gemini OR DeepSeek OR 英伟达) (发布 OR 开源 OR 融资 OR 监管 OR 收购 OR 上线 OR 辞职 OR 离任)'),
      { name: '量子位', url: 'https://www.qbitai.com/feed' },
      { name: 'IT之家', url: 'https://www.ithome.com/rss/' },
      { name: 'Solidot', url: 'https://www.solidot.org/index.rss' }
    ],
    topic: /人工智能|大模型|大语言模型|生成式|机器人|算力|英伟达|英偉達|黄仁勋|黃仁勳|芯片|OpenAI|ChatGPT|Codex|GPT|Anthropic|Claude|Gemini|DeepSeek|(?:^|[^a-z])AI(?![a-z])/i,
    major: /发布|發布|推出|上线|上線|开源|開源|首发|融资|融資|并购|併購|收购|监管|監管|限制|禁令|漏洞|中断|中斷|离任|離任|辞职|辭職|换帅|換帥|IPO/i
  },
  {
    category: '影视',
    feeds: [
      chinaNews('culture'),
      searchNews('(电影 OR 剧集 OR 影视) (票房 OR 电影节 OR 获奖 OR 并购 OR 定档 OR 撤档)'),
      { name: '百度新闻', url: 'https://news.baidu.com/n?cmd=1&class=enternews&tn=rss' }
    ],
    topic: /电影|電影|影视|影視|剧集|劇集|电视剧|電視劇|票房|流媒体|流媒體|制片|製片|院线|院線|影帝|影后|奥斯卡|奧斯卡/,
    major: /票房|获奖|獲獎|最佳影片|大奖|大獎|入围|入圍|(电影节|電影節|电影表演艺术周).{0,16}(开幕|開幕|闭幕|閉幕|揭晓|揭曉)|并购|併購|收购|收購|撤档|撤檔|定档|定檔|首部.{0,12}院线电影.{0,5}上映/
  }
];

const PROMO_RE =
  /招聘启事|招聘公告|征稿启事|投稿须知|优惠券|限时优惠|培训招生|广告合作|商务合作|诚邀报名|课程促销|强势来袭|开启预订|品牌钜惠|招商推介|开箱|上手体验|盲测超越/;
const LOW_SIGNAL = /周报|週報|日报|日報|早报|早報|盘点|盤點|攻略|专访|專訪|妙喻|花絮|路透照|种草|感受.{0,10}故事|文化新观察|评论员文章|观影指南|值得一看|开发者猜测/;

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

function parseFeed(xml, feed) {
  const blocks =
    xml.match(/<item\b[^>]*>[\s\S]*?<\/item>/gi) ||
    xml.match(/<entry\b[^>]*>[\s\S]*?<\/entry>/gi) ||
    [];

  const items = [];

  for (const [rank, block] of blocks.slice(0, 40).entries()) {
    const source = readTag(block, 'source') || feed.name;
    const rawTitle = readTag(block, 'title');
    const suffix = ' - ' + source;
    const title = feed.aggregator && rawTitle.endsWith(suffix) ? rawTitle.slice(0, -suffix.length) : rawTitle;

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
      publishedAt,
      rank: feed.aggregator ? rank : null
    });
  }

  return items;
}

// ===== 网络获取 =====

async function fetchRss(feed) {
  const controller = new AbortController();

  const timer = setTimeout(
    () => controller.abort(),
    FETCH_TIMEOUT_MS
  );

  try {
    const response = await fetch(feed.url, {
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
    const items = parseFeed(xml, feed);

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
    .replace(/黃/g, '黄').replace(/勳/g, '勋').replace(/離/g, '离')
    .replace(/辭/g, '辞').replace(/職/g, '职').replace(/偉/g, '伟')
    .toLowerCase()
    .replace(/[\s\p{P}\p{S}]/gu, '');
}

function sameStory(a, b) {
  if (a === b || (Math.min(a.length, b.length) >= 8 && (a.includes(b) || b.includes(a)))) return true;
  const aiFilm = /首部.{0,20}ai.{0,4}电影/;
  if (aiFilm.test(a) && aiFilm.test(b)) return true;
  const departure = /辞职|离任|离职|卸任|换帅/g;
  const eventKey = (title) => title.replace(departure, '离职').match(/[\p{L}\p{N}]{3}离职/gu) ?? [];
  const departures = eventKey(b);
  if (eventKey(a).some(key => departures.includes(key))) return true;
  const phrases = new Set(Array.from({ length: Math.max(0, a.length - 3) }, (_, i) => a.slice(i, i + 4)));
  let shared = 0;
  for (const phrase of phrases) if (b.includes(phrase)) shared++;
  return shared >= 5 && shared >= 0.5 * (Math.min(a.length, b.length) - 3);
}

function priority(item, group, now) {
  const age = Math.max(0, now - item.publishedAt) / HOUR_MS;
  const major = group.major.test(item.title);
  const prominence = item.rank === null ? 0 : Math.max(0, 10 - item.rank / 3);
  const milestone = (
    (group.category === '影视' && /票房.{0,5}(破|超|逾|达|达成)\s*[\d.]+\s*亿|最佳影片|金鸡奖|奥斯卡|收购/.test(item.title))
      || (group.category === '国内' && /医保药品目录|降准|降息|APEC.{0,12}(开幕|闭幕|会晤|声明)|会议.{0,8}筹备.{0,4}就绪/i.test(item.title))
      || (group.category === 'AI' && /监管|監管|禁令|并购|併購|IPO|换帅|CEO.{0,10}(离任|辞职)|(?:大模型|新模型|新一代模型|API).{0,14}(发布|發布|开源)|(?:发布|發布|开放).{0,14}(大模型|新模型|新一代模型|API)/i.test(item.title))
  );
  const briefing = /解读|观察|評論|评论|侧记|現場|现场|如何看待|票房.{0,4}(遇冷|仅|僅|惨淡)|未破万/.test(item.title);
  return (major ? 30 : 0) + (milestone ? 12 : 0) + prominence
    + (age < 24 ? 6 : 0) - age / 8 - (briefing ? 12 : 0);
}

// ===== 每类收集候选新闻 =====

async function collectGroup(group, now) {
  const results = await Promise.all(
    group.feeds.map(async feed => {
      try {
        const items = await fetchRss(feed);

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
    if (item.publishedAt === null || LOW_SIGNAL.test(item.title)) return false;
    if (group.topic && (!group.topic.test(item.title) || !group.major.test(item.title))) return false;

    const age = now - item.publishedAt;

    return age >= -5 * 60 * 1000 && age <= MAX_AGE_MS;
  });

  const errors = results
    .filter(result => !result.ok)
    .map(result => result.error);

  return {
    category: group.category,
    candidates: recent.map(item => ({
      ...item,
      key: normalizeTitle(item.title),
      work: group.category === '影视' ? item.title.match(/《([^》]{2,24})》/)?.[1] : undefined,
      score: priority(item, group, now)
    }))
      .sort((a, b) => b.score - a.score || b.publishedAt - a.publishedAt),
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
  const seen = [];

  for (let round = 0; round < 2; round++) {
    for (const group of groups) {
      const selected = group.candidates.find(item => !seen.some(story =>
        sameStory(story.key, item.key) || (story.work && story.work === item.work)));
      if (selected) {
        seen.push(selected);
        items.push({
          title: `【${group.category}】${selected.title}`,
          category: group.category,
          source: selected.source,
          url: selected.url,
          publishedAt: selected.publishedAt
        });
      } else if (round === 0) {
        const message = group.available ? '近48小时暂无可用新闻' : '新闻源暂不可用';
        items.push({
          title: `【${group.category}】${message}`,
          category: group.category,
          source: 'DayPane',
          url: '',
          publishedAt: null,
          unavailable: true
        });
      }
    }
  }

  return {
    ok: seen.length > 0,
    items,
    source: '综合 RSS',
    error: groups.flatMap(group => group.errors).join('; ') || null
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
