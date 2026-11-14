import assert from 'node:assert/strict';
import test from 'node:test';
import { getNews } from '../src/providers/news.js';

test('Chinese news covers each category before adding unique second stories', async (t) => {
  const rss = (...stories) => '<rss><channel>' + stories.map(([title, hoursAgo]) =>
    '<item><title>' + title + '</title><pubDate>' +
    new Date(Date.now() - hoursAgo * 3600000).toUTCString() +
    '</pubDate></item>').join('') + '</channel></rss>';
  const feeds = {
    '/rss/society.xml': rss(['社会新闻的最新进展', 1], ['社会新闻的后续报道', 26]),
    '/rss/world.xml': rss(['社会新闻的最新进展！', 2], ['国际新闻的最新进展', 3], ['超过两天的国际新闻', 50]),
    '/rss/finance.xml': rss(['财经新闻的最新进展', 1], ['财经新闻的后续报道', 2], ['财经新闻第三条报道', 3]),
    '/index.rss': rss(['科技新闻的最新进展', 1], ['科技新闻的后续报道', 2])
  };
  t.mock.method(globalThis, 'fetch', async (url) => {
    const xml = feeds[new URL(url).pathname];
    return xml ? new Response(xml) : new Response(null, { status: 503 });
  });

  const news = await getNews();
  assert.equal(news.ok, true);
  assert.deepEqual(news.items.map(item => item.title), [
    '【社会】社会新闻的最新进展', '【国际】国际新闻的最新进展',
    '【财经】财经新闻的最新进展', '【科技】科技新闻的最新进展',
    '【社会】社会新闻的后续报道', '【财经】财经新闻的后续报道', '【科技】科技新闻的后续报道'
  ]);
});
