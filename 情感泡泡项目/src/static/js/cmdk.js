/* ===== cmdk.js — ⌘K 命令面板 =====
   一个入口，三类结果：操作 / 情绪筛选 / 泡泡。
   搜索是本地模糊匹配（子串优先，其次子序列），命中字符用 <mark> 高亮；
   泡泡索引直接读 DOM，所以轮询新增的泡泡无需重建索引，打开面板时现取即可。

   键盘：↑↓ 选择、↵ 执行、Esc 关闭、Tab 锁在面板内。
*/
window.CmdK = (function () {
  'use strict';

  var root, input, list, panel;
  var items = [];          // 当前渲染的扁平列表（与 DOM 顺序一致）
  var active = 0;
  var trapHandle = null;
  var locked = false;
  var RECENT_KEY = 'eb-cmdk-recent';

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 模糊打分：返回 {score, hits:[index...]}；0 分表示不匹配 */
  function match(text, q) {
    if (!q) return { score: 1, hits: [] };
    var t = text.toLowerCase(), s = q.toLowerCase();
    var at = t.indexOf(s);
    if (at >= 0) {
      var hits = [];
      for (var i = 0; i < s.length; i++) hits.push(at + i);
      // 开头命中 > 靠前命中；短文本更精确
      return { score: 1000 - at * 3 + (at === 0 ? 240 : 0) + Math.max(0, 40 - t.length), hits: hits };
    }
    // 子序列：按顺序出现的字符，允许中间跳过
    var ti = 0, hit = [], gap = 0, last = -1;
    for (var j = 0; j < s.length; j++) {
      var found = t.indexOf(s[j], ti);
      if (found < 0) return { score: 0, hits: [] };
      if (last >= 0) gap += found - last - 1;
      last = found;
      hit.push(found);
      ti = found + 1;
    }
    return { score: 260 - gap * 4, hits: hit };
  }

  function highlight(text, hits) {
    if (!hits || !hits.length) return esc(text);
    var set = {}, out = '', open = false;
    hits.forEach(function (i) { set[i] = true; });
    for (var i = 0; i < text.length; i++) {
      if (set[i] && !open) { out += '<mark>'; open = true; }
      if (!set[i] && open) { out += '</mark>'; open = false; }
      out += esc(text[i]);
    }
    return open ? out + '</mark>' : out;
  }

  /* ---------- 数据源 ---------- */
  function commands() {
    var out = [
      {
        id: 'cmd-publish', group: '操作', icon: '＋', title: '发布新泡泡',
        hint: '写下此刻的心情', keys: '发布 新建 心情 publish',
        run: function () { if (window.openPublish) window.openPublish(); }
      },
      {
        id: 'cmd-insights', group: '操作', icon: '📊', title: '打开情绪洞察',
        hint: '看看这段时间的情绪构成', keys: '洞察 统计 图表 分析 insight',
        run: function () { if (window.Insights) window.Insights.open(); }
      },
      {
        id: 'cmd-conversations', group: '操作', icon: '💬', title: '我的对话',
        hint: '继续未说完的话', keys: '消息 聊天 会话 chat',
        run: function () { if (window.openSidebar) window.openSidebar('/partial/panel/conversations'); }
      },
      {
        id: 'cmd-theme', group: '操作', icon: '🌗', title: '切换深浅色',
        hint: '跟随系统，或手动锁定', keys: '主题 深色 浅色 夜间 theme dark light',
        run: function () {
          var btn = document.getElementById('theme-toggle');
          if (btn) btn.click();
        }
      },
      {
        id: 'cmd-reset-order', group: '操作', icon: '↺', title: '恢复默认泡泡顺序',
        hint: '清除你拖拽排好的布局', keys: '重置 排序 布局 reset order',
        run: function () {
          if (window.WallOrder && window.WallOrder.reset()) {
            UI.toast('已恢复默认顺序', { type: 'success' });
          }
        }
      }
    ];
    return out;
  }

  function emotionItems() {
    var meta = window.EB || {};
    var emotions = meta.emotions || [];
    var icons = meta.icons || {};
    return emotions.map(function (e) {
      return {
        id: 'emo-' + e, group: '情绪', icon: icons[e] || '•', title: '只看「' + e + '」',
        hint: '筛选泡泡墙', keys: e,
        run: function () { location.href = '/?emotion=' + encodeURIComponent(e); }
      };
    });
  }

  function bubbleItems() {
    var nodes = document.querySelectorAll('.soap[data-bubble-id]');
    return Array.prototype.map.call(nodes, function (n) {
      var content = (n.getAttribute('data-content') || '').trim();
      var emotion = n.getAttribute('data-emotion') || '';
      var author = n.getAttribute('data-author') || '';
      var icons = (window.EB && window.EB.icons) || {};
      return {
        id: 'bubble-' + n.getAttribute('data-bubble-id'),
        group: '泡泡', icon: icons[emotion] || '💭',
        title: content || '（无内容）',
        hint: (author ? '@' + author : '') + (emotion ? ' · ' + emotion : ''),
        keys: content + ' ' + emotion + ' ' + author,
        run: function () {
          if (window.openSidebar) window.openSidebar('/partial/panel/bubble/' + n.getAttribute('data-bubble-id'));
        }
      };
    });
  }

  /* ---------- 渲染 ---------- */
  function render(q) {
    var all = commands().concat(emotionItems()).concat(bubbleItems());
    var recentIds = UI.store.get(RECENT_KEY, []);
    var groups = [];

    if (!q) {
      var recent = recentIds.map(function (id) {
        return all.filter(function (it) { return it.id === id; })[0];
      }).filter(Boolean).slice(0, 4);
      if (recent.length) groups.push({ name: '最近使用', items: recent.map(function (it) {
        return { item: it, hits: [] };
      }) });
      ['操作', '情绪'].forEach(function (g) {
        groups.push({ name: g, items: all.filter(function (it) { return it.group === g; })
          .map(function (it) { return { item: it, hits: [] }; }) });
      });
      var bubbles = all.filter(function (it) { return it.group === '泡泡'; }).slice(0, 6);
      if (bubbles.length) groups.push({ name: '泡泡', items: bubbles.map(function (it) {
        return { item: it, hits: [] };
      }) });
    } else {
      var scored = all.map(function (it) {
        var m = match(it.title, q);
        var mk = match(it.keys || '', q);
        // 高亮只认标题里的命中：keys 只负责"能搜到"，字符位置对不上标题就没法标
        return { item: it, score: Math.max(m.score, mk.score), hits: m.score > 0 ? m.hits : [] };
      }).filter(function (r) { return r.score > 0; })
        .sort(function (a, b) { return b.score - a.score; });

      ['操作', '情绪', '泡泡'].forEach(function (g) {
        var rows = scored.filter(function (r) { return r.item.group === g; });
        if (g === '泡泡') rows = rows.slice(0, 8);
        if (rows.length) groups.push({ name: g, items: rows });
      });
    }

    items = [];
    active = 0;
    list.innerHTML = '';

    if (!groups.length) {
      var empty = UI.el('div', 'cmdk-empty');
      empty.appendChild(UI.el('p', 'cmdk-empty-title', '没有找到「' + q + '」'));
      empty.appendChild(UI.el('p', 'cmdk-empty-hint', '试试情绪标签，或者直接发布一颗新泡泡'));
      list.appendChild(empty);
      input.setAttribute('aria-activedescendant', '');
      return;
    }

    groups.forEach(function (g) {
      var head = UI.el('div', 'cmdk-group', g.name);
      head.setAttribute('role', 'presentation');
      list.appendChild(head);
      g.items.forEach(function (row) {
        var it = row.item;
        var idx = items.length;
        items.push(it);
        var node = document.createElement('div');
        node.className = 'cmdk-item';
        node.id = 'cmdk-opt-' + idx;
        node.setAttribute('role', 'option');
        node.setAttribute('aria-selected', idx === 0 ? 'true' : 'false');
        node.dataset.index = String(idx);

        var ico = UI.el('span', 'cmdk-ico', it.icon);
        ico.setAttribute('aria-hidden', 'true');
        var main = UI.el('span', 'cmdk-main');
        main.innerHTML = '<span class="cmdk-title">' + highlight(it.title, row.hits) + '</span>'
          + (it.hint ? '<span class="cmdk-hint">' + esc(it.hint) + '</span>' : '');
        var go = UI.el('span', 'cmdk-go', '↵');
        go.setAttribute('aria-hidden', 'true');

        node.appendChild(ico);
        node.appendChild(main);
        node.appendChild(go);
        node.addEventListener('mousemove', function () { setActive(idx, false); });
        node.addEventListener('click', function () { execute(idx); });
        list.appendChild(node);
      });
    });
    setActive(0, false);
  }

  function setActive(idx, scroll) {
    if (!items.length) return;
    idx = Math.max(0, Math.min(items.length - 1, idx));
    var prev = list.querySelector('.cmdk-item[aria-selected="true"]');
    if (prev) prev.setAttribute('aria-selected', 'false');
    active = idx;
    var node = list.querySelector('.cmdk-item[data-index="' + idx + '"]');
    if (node) {
      node.setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', node.id);
      if (scroll !== false) node.scrollIntoView({ block: 'nearest' });
    }
  }

  function execute(idx) {
    var it = items[idx];
    if (!it) return;
    var recent = UI.store.get(RECENT_KEY, []);
    recent = [it.id].concat(recent.filter(function (x) { return x !== it.id; })).slice(0, 6);
    UI.store.set(RECENT_KEY, recent);
    close();
    // 等面板收起、焦点归还后再执行，避免"打开侧栏的同时焦点被抢回"
    setTimeout(function () { it.run(); }, 0);
  }

  /* ---------- 开关 ---------- */
  function open() {
    if (!root || !root.hidden) return;
    root.hidden = false;
    root.classList.add('is-open');
    document.body.classList.add('cmdk-open');
    if (!locked) { locked = true; UI.lockScroll(true); }
    render('');
    input.value = '';
    trapHandle = UI.trap(panel, {
      initial: input,
      onEscape: function () { close(); }
    });
    requestAnimationFrame(function () { input.focus({ preventScroll: true }); });
  }

  function close() {
    if (!root || root.hidden) return;
    root.classList.remove('is-open');
    root.hidden = true;
    document.body.classList.remove('cmdk-open');
    if (locked) { locked = false; UI.lockScroll(false); }
    if (trapHandle) { trapHandle.release(); trapHandle = null; }
  }

  function toggle() { if (root.hidden) open(); else close(); }

  function bind() {
    root = document.getElementById('cmdk');
    if (!root) return;
    panel = root.querySelector('.cmdk-panel');
    input = root.querySelector('.cmdk-input');
    list = root.querySelector('.cmdk-list');

    root.querySelectorAll('[data-cmdk-close]').forEach(function (n) {
      n.addEventListener('click', close);
    });

    input.addEventListener('input', function () { render(input.value.trim()); });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); execute(active); }
      else if (e.key === 'Home' && e.metaKey) { e.preventDefault(); setActive(0); }
      else if (e.key === 'End' && e.metaKey) { e.preventDefault(); setActive(items.length - 1); }
    });

    // 全局：⌘K / Ctrl+K 开关；/ 在非输入态直接打开
    document.addEventListener('keydown', function (e) {
      var mod = e.metaKey || e.ctrlKey;
      if (mod && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        toggle();
        return;
      }
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        var t = e.target;
        var typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
        if (!typing) { e.preventDefault(); open(); }
      }
    });

    // 点击顶栏入口
    document.querySelectorAll('[data-cmdk-open]').forEach(function (n) {
      n.addEventListener('click', function (e) { e.preventDefault(); open(); });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();

  return { open: open, close: close, toggle: toggle };
})();
