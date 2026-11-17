/* global document, fetch, URL, AbortController, clearTimeout, setTimeout */
const labels = {
  news: '今日新闻', ai: 'AI 用量', weather: '北京天气', market: '市场',
  clocks: '世界时钟', mlb: 'MLB 道奇 / 教士', quote: '今日一句',
};
const $ = id => document.getElementById(id);
const list = $('modules');
const save = $('save');
const reset = $('reset');
const limit = $('news-limit');
const img = $('preview-image');
const notice = $('preview-status');
const status = $('save-status');
let state, order, active, saved, revision, ready, saving, dragged;
let timer, pending, previewUrl;

function draft() {
  return { modules: order.filter(key => active.has(key)), newsLimit: Number(limit.value) };
}

function showStatus(text, error = false) {
  status.textContent = text;
  status.toggleAttribute('data-error', error);
}

function controls() {
  save.disabled = !state.connected || !ready || saving || JSON.stringify(draft()) === JSON.stringify(saved);
  limit.disabled = !active.has('news') || saving;
  reset.disabled = Boolean(saving);
}

function move(key, target, focusClass) {
  if (target < 0 || target >= order.length || saving) return;
  order.splice(order.indexOf(key), 1);
  order.splice(target, 0, key);
  showRows();
  if (focusClass) {
    const row = list.querySelector(`[data-module="${key}"]`);
    const button = row.querySelector(focusClass);
    (button.disabled ? row.querySelector('input') : button).focus();
  }
  changed();
}

function showRows() {
  list.replaceChildren();
  for (const [i, key] of order.entries()) {
    const node = $('row').content.cloneNode(true);
    const row = node.querySelector('li');
    row.dataset.module = key;
    row.toggleAttribute('data-off', !active.has(key));
    const checkbox = row.querySelector('input');
    checkbox.checked = active.has(key);
    checkbox.disabled = Boolean(saving);
    row.querySelector('label span').textContent = labels[key];
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) active.add(key);
      else active.delete(key);
      row.toggleAttribute('data-off', !checkbox.checked);
      changed();
    });
    for (const [name, offset, word] of [['up', -1, '上移'], ['down', 1, '下移']]) {
      const button = row.querySelector('.' + name);
      button.setAttribute('aria-label', word + labels[key]);
      button.disabled = saving || i + offset < 0 || i + offset >= order.length;
      button.addEventListener('click', () => move(key, i + offset, '.' + name));
    }
    const grip = row.querySelector('.grip');
    grip.draggable = !saving;
    grip.addEventListener('dragstart', event => {
      dragged = key;
      event.dataTransfer.setData('text/plain', key);
      event.dataTransfer.effectAllowed = 'move';
    });
    grip.addEventListener('dragend', () => { dragged = null; });
    row.addEventListener('dragover', event => {
      if (dragged && dragged !== key) {
        event.preventDefault();
        row.classList.add('drop-target');
        event.dataTransfer.dropEffect = 'move';
      }
    });
    row.addEventListener('dragleave', event => {
      if (!row.contains(event.relatedTarget)) row.classList.remove('drop-target');
    });
    row.addEventListener('drop', event => {
      event.preventDefault();
      row.classList.remove('drop-target');
      if (dragged && dragged !== key) move(dragged, i);
      dragged = null;
    });
    list.append(node);
  }
}

async function post(path, layout, signal) {
  const res = await fetch('/api/' + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ layout, revision }),
  });
  if (!res.ok) throw new Error((await res.json()).error);
  return res;
}

async function refreshPreview() {
  const request = new AbortController();
  pending = request;
  try {
    const res = await post('preview', draft(), request.signal);
    const blob = await res.blob();
    if (pending !== request) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob);
    img.src = previewUrl;
    img.hidden = false;
    notice.hidden = true;
    ready = true;
  } catch (error) {
    if (pending !== request || request.signal.aborted) return;
    notice.textContent = error.message;
    notice.toggleAttribute('data-error', true);
  }
  controls();
}

function changed() {
  ready = false;
  controls();
  pending?.abort();
  clearTimeout(timer);
  notice.textContent = '正在生成预览…';
  notice.removeAttribute('data-error');
  notice.hidden = false;
  showStatus(!state.connected ? '暂时无法连接 GitHub。可以预览，保存前请重新打开网页。'
    : JSON.stringify(draft()) === JSON.stringify(saved) ? '当前为已保存的版式。' : '有更改尚未保存。');
  timer = setTimeout(refreshPreview, 160);
}

function apply(layout) {
  active = new Set(layout.modules);
  order = [...layout.modules, ...state.modules.filter(module => !active.has(module))];
  limit.value = layout.newsLimit;
  showRows();
  changed();
}

limit.addEventListener('change', changed);
reset.addEventListener('click', () => apply(state.default));
save.addEventListener('click', async () => {
  saving = true;
  controls();
  showRows();
  showStatus('正在保存…');
  const layout = draft();
  try {
    revision = (await (await post('save', layout)).json()).revision;
    saved = layout;
    showStatus('版式已保存。下一次云端出图时生效。');
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    saving = false;
    showRows();
    controls();
  }
});

async function load() {
  try {
    const response = await fetch('/api/layout');
    if (!response.ok) throw new Error();
    state = await response.json();
    saved = state.layout;
    revision = state.revision;
    apply(saved);
  } catch {
    notice.textContent = '编辑器暂不可用，请重新打开网页。';
    showStatus(notice.textContent, true);
  }
}

load();
