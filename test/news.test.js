import assert from 'node:assert/strict';
import test from 'node:test';
import { getNews } from '../src/providers/news.js';

test('hot list follows aggregate rank and removes repeated titles', async (t) => {
  const stamp = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 16).replace('T', ' ');
  const ranked = ['第一条 &amp; 热搜', '第一条 &amp; 热搜', '第二条热搜',
    ...Array.from({ length: 8 }, (_, i) => '话题' + i)];
  const html = '数据更新于 ' + stamp + '<ol class="rows" id="list">' + ranked.map(title =>
    '<li><a class="row-title" href="https://example.test/topic">' + title + '</a></li>').join('') + '</ol>';
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(new URL(url).hostname, 'tunbang.com');
    return new Response(html);
  });
  const news = await getNews();
  assert.equal(news.ok, true);
  assert.equal(news.source, '豚榜');
  assert.equal(news.items.length, 8);
  assert.deepEqual(news.items.slice(0, 2).map(item => item.title), ['第一条 & 热搜', '第二条热搜']);
});
