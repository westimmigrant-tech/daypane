import assert from 'node:assert/strict';
import test from 'node:test';
import { getNews } from '../src/providers/news.js';

test('Chinese news ranks significant events and covers four categories without duplicate stories', async (t) => {
  const rss = (...stories) => '<rss><channel>' + stories.map(([title, hoursAgo, source]) =>
    '<item><title>' + title + '</title><pubDate>' +
    new Date(Date.now() - hoursAgo * 3600000).toUTCString() +
    '</pubDate>' + (source ? '<source>' + source + '</source>' : '') + '</item>').join('') + '</channel></rss>';
  const feeds = {
    '/rss/world.xml': rss(['海外小镇今日举办街头艺术展', 1], ['两国正式签署停火协议', 4], ['美联储央行会议宣布降息', 26]),
    '/rss/china.xml': rss(['国务院发布全国医疗新规', 4], ['地方文旅活动今天正式开跑', 1]),
    '/rss/society.xml': rss(['新疆库车市发生5.0级地震', 5], ['中国南部超过两天的地震', 50]),
    '/feed': rss(['人工智能峰会开启预订', 1], ['Anthropic发布新模型Claude - 量子位', 4]),
    '/rss/culture.xml': rss(['新开幕的音乐展吸引众多观众', 1], ['电影《乙片》获得最佳影片', 4])
  };
  t.mock.method(globalThis, 'fetch', async (url) => {
    const parsed = new URL(url);
    if (parsed.hostname === 'news.google.com') {
      const query = parsed.searchParams.get('q');
      const xml = query.includes('Anthropic')
        ? rss(['Anthropic发布新模型Claude - 媒体甲', 2, '媒体甲'], ['人工智能监管新规发布 - 媒体乙', 6, '媒体乙'])
        : rss(['电影《甲片》票房突破10亿 - 媒体丙', 1, '媒体丙'], ['《甲片》内地累计票房突破10亿 - 媒体丁', 2, '媒体丁']);
      return new Response(xml);
    }
    const xml = feeds[parsed.pathname];
    return xml ? new Response(xml) : new Response(null, { status: 503 });
  });

  const news = await getNews();
  assert.equal(news.ok, true);
  assert.deepEqual(news.items.map(item => item.category), ['国际', '国内', 'AI', '影视', '国际', '国内', 'AI', '影视']);
  const titles = news.items.map(item => item.title).join('\n');
  assert.match(titles, /停火协议/);
  assert.match(titles, /国务院发布/);
  assert.match(titles, /人工智能监管/);
  assert.match(titles, /最佳影片/);
  assert.equal(news.items.filter(item => item.title.includes('甲片')).length, 1);
  assert.equal(news.items.filter(item => item.title.includes('Claude')).length, 1);
  assert.doesNotMatch(titles, /街头艺术展|开启预订|超过两天| - 媒体/);
});
