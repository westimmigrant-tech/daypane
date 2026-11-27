import assert from 'node:assert/strict';
import test from 'node:test';
import { getNews } from '../src/providers/news.js';

test('hot list follows the Rebang home page and ignores summaries and repeated links', async (t) => {
  const items = [['第一条 &amp; 热搜', 1], ['同一条不同文字', 1], ['第二条热搜', 2],
    ...Array.from({ length: 8 }, (_, i) => ['话题' + i, i + 3])];
  const html = '<a href="https://example.test/summary">摘要</a>' + items.map(([title, id]) =>
    '<div><a data-item-title-link="true" href="https://example.test/' + id + '">' + title + '</a></div>').join('');
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, 'https://rebang.today/?tab=top');
    return new Response(html);
  });
  const news = await getNews();
  assert.equal(news.ok, true);
  assert.equal(news.source, '今日热榜');
  assert.equal(news.items.length, 8);
  assert.deepEqual(news.items.slice(0, 2).map(item => item.title), ['第一条 & 热搜', '第二条热搜']);
});
