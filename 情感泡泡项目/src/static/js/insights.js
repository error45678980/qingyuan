/* ===== insights.js — 情绪洞察面板 =====
   数据来自 /api/insights：每条泡泡只给 (情绪, epoch 毫秒)，
   按访问者本地时区分桶——服务端分桶会让时区整体错位。

   四个可视化都是手写 DOM/SVG，不引图表库（项目规则：零外部 CDN）：
   - 统计卡：数字滚动
   - 堆叠柱：高度过渡 + 悬停提示 + 图例筛选
   - 环形图：stroke-dasharray 分段，悬停放大并更新圆心
   - 24 小时热力条：高度编码 + 悬停提示
*/
window.Insights = (function () {
  'use strict';

  var root, panel, trapHandle = null;
  var locked = false;
  var data = null;              // {totals, points, generated_at}
  var fetchedAt = 0;
  var range = 30;
  var hidden = {};              // 被图例关掉的情绪
  var H = 200;                  // 柱状图绘图区高度（px）
  var CACHE_MS = 60 * 1000;

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function dayKey(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
  function addDays(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }

  function emotions() { return (window.EB && window.EB.emotions) || []; }
  function icons() { return (window.EB && window.EB.icons) || {}; }
  function colorOf(tag) {
    var c = (window.EB && window.EB.colors) || {};
    return c[tag] || 'var(--color-brand)';
  }

  function accent(node, tag) {
    node.style.setProperty('--accent', colorOf(tag));
  }

  /* ---------- 派生数据 ---------- */
  function series() {
    var points = (data && data.points) || [];
    var today = startOfDay(new Date());
    var start = addDays(today, -(range - 1));
    var buckets = [];
    var index = {};
    for (var i = 0; i < range; i++) {
      var d = addDays(start, i);
      var b = { date: d, key: dayKey(d), counts: {}, total: 0 };
      buckets.push(b);
      index[b.key] = b;
    }
    points.forEach(function (p) {
      var d = new Date(p.t);
      var b = index[dayKey(d)];
      if (!b) return;
      b.counts[p.e] = (b.counts[p.e] || 0) + 1;
      b.total += 1;
    });
    return buckets;
  }

  function counts() {
    var out = {};
    emotions().forEach(function (e) { out[e] = 0; });
    ((data && data.points) || []).forEach(function (p) {
      if (out[p.e] == null) out[p.e] = 0;
      out[p.e] += 1;
    });
    return out;
  }

  function hours() {
    var out = new Array(24).fill(0);
    ((data && data.points) || []).forEach(function (p) {
      out[new Date(p.t).getHours()] += 1;
    });
    return out;
  }

  function weekDelta() {
    var points = (data && data.points) || [];
    var now = new Date();
    var day = startOfDay(now);
    var weekStart = addDays(day, -6).getTime();
    var prevStart = addDays(day, -13).getTime();
    var cur = 0, prev = 0;
    points.forEach(function (p) {
      if (p.t >= weekStart) cur++;
      else if (p.t >= prevStart) prev++;
    });
    return { cur: cur, prev: prev };
  }

  /* ---------- 骨架屏 ---------- */
  function skeleton() {
    var body = root.querySelector('.ins-body');
    body.innerHTML = '';
    var stats = UI.el('div', 'stat-grid');
    for (var i = 0; i < 4; i++) {
      var s = UI.el('div', 'stat is-loading');
      s.appendChild(UI.el('span', 'sk-line sk-sm'));
      s.appendChild(UI.el('span', 'sk-line sk-lg'));
      stats.appendChild(s);
    }
    var grid = UI.el('div', 'ins-grid');
    for (var j = 0; j < 2; j++) {
      var c = UI.el('div', 'chart-card is-loading');
      c.appendChild(UI.el('span', 'sk-line sk-md'));
      c.appendChild(UI.el('span', 'sk-block'));
      grid.appendChild(c);
    }
    body.appendChild(stats);
    body.appendChild(grid);
  }

  /* ---------- 统计卡 ---------- */
  function renderStats(host) {
    var c = counts();
    var total = Object.keys(c).reduce(function (a, k) { return a + c[k]; }, 0);
    var delta = weekDelta();
    var t = (data && data.totals) || {};

    var cards = [
      { label: '泡泡总数', value: total, note: '当前仍在墙上的' },
      {
        label: '本周新增', value: delta.cur,
        note: delta.prev ? '上周 ' + delta.prev + ' 颗' : '上周还没有',
        tone: delta.cur > delta.prev ? 'up' : (delta.cur < delta.prev ? 'down' : '')
      },
      { label: '对话数', value: t.conversations || 0, note: (t.messages || 0) + ' 条消息' },
      { label: '已解决', value: t.resolved || 0, note: '被作者炸掉的泡泡' }
    ];

    host.innerHTML = '';
    cards.forEach(function (card) {
      var n = UI.el('div', 'stat');
      n.appendChild(UI.el('span', 'stat-label', card.label));
      var num = UI.el('span', 'stat-num', '0');
      n.appendChild(num);
      var note = UI.el('span', 'stat-note' + (card.tone ? ' is-' + card.tone : ''), card.note);
      n.appendChild(note);
      host.appendChild(n);
      UI.countUp(num, card.value, 640);
    });
  }

  /* ---------- 堆叠柱 ---------- */
  function renderTrend(host) {
    host.innerHTML = '';
    var buckets = series();
    var list = emotions().filter(function (e) { return !hidden[e]; });
    var max = 0;
    buckets.forEach(function (b) {
      var sum = 0;
      list.forEach(function (e) { sum += b.counts[e] || 0; });
      if (sum > max) max = sum;
    });

    var chart = UI.el('div', 'trend');
    chart.setAttribute('role', 'img');
    chart.setAttribute('aria-label', '最近 ' + range + ' 天每天的发布量，最高 ' + max + ' 颗');

    var plot = UI.el('div', 'trend-plot');
    var tip = UI.el('div', 'chart-tip');
    tip.hidden = true;

    var labelEvery = range <= 7 ? 1 : (range <= 14 ? 2 : 5);

    buckets.forEach(function (b, i) {
      var col = UI.el('div', 'trend-col');
      col.setAttribute('data-i', String(i));
      var stack = UI.el('div', 'trend-stack');
      stack.style.height = H + 'px';

      var stackTotal = 0;
      list.forEach(function (e) { stackTotal += b.counts[e] || 0; });

      list.forEach(function (e) {
        var n = b.counts[e] || 0;
        if (!n) return;
        var seg = UI.el('span', 'trend-seg');
        accent(seg, e);
        seg.style.height = '0px';
        seg.title = e + ' · ' + n;
        stack.appendChild(seg);
        var target = Math.max(3, Math.round(n / max * H));
        requestAnimationFrame(function () { seg.style.height = target + 'px'; });
      });

      var x = UI.el('span', 'trend-x', (i % labelEvery === 0)
        ? (b.date.getMonth() + 1) + '/' + b.date.getDate() : '');
      col.appendChild(stack);
      col.appendChild(x);

      col.addEventListener('pointerenter', function () {
        tip.hidden = false;
        tip.innerHTML = '';
        tip.appendChild(UI.el('strong', '', (b.date.getMonth() + 1) + ' 月 ' + b.date.getDate() + ' 日'));
        tip.appendChild(UI.el('span', 'chart-tip-total', '共 ' + stackTotal + ' 颗'));
        if (!stackTotal) {
          tip.appendChild(UI.el('span', 'chart-tip-empty', '这天还没有泡泡'));
        }
        list.forEach(function (e) {
          var n = b.counts[e] || 0;
          if (!n) return;
          var row = UI.el('span', 'chart-tip-row');
          var dot = UI.el('i', 'dot');
          accent(dot, e);
          row.appendChild(dot);
          row.appendChild(UI.el('span', '', e));
          row.appendChild(UI.el('b', '', String(n)));
          tip.appendChild(row);
        });
        // 提示框躲开指针所在的那半边：左半边的柱子 → 提示放右，反之放左
        var tw = tip.offsetWidth, cw = chart.clientWidth;
        var center = col.offsetLeft + col.offsetWidth / 2;
        var left = center > cw / 2 ? center - tw - 14 : center + 14;
        tip.style.left = Math.max(0, Math.min(cw - tw, left)) + 'px';
        tip.style.right = 'auto';
      });
      col.addEventListener('pointerleave', function () { tip.hidden = true; });

      plot.appendChild(col);
    });

    if (!max) {
      var empty = UI.el('div', 'chart-empty');
      empty.appendChild(UI.el('span', '', '🌙'));
      empty.appendChild(UI.el('p', '', '这段时间还没有泡泡'));
      plot.appendChild(empty);
    }

    chart.appendChild(plot);
    chart.appendChild(tip);
    host.appendChild(chart);
  }

  /* ---------- 环形图 ---------- */
  function renderDonut(host, legendHost) {
    host.innerHTML = '';
    legendHost.innerHTML = '';
    var c = counts();
    var list = emotions().filter(function (e) { return !hidden[e]; });
    var total = list.reduce(function (a, e) { return a + (c[e] || 0); }, 0);

    var wrap = UI.el('div', 'donut-wrap');
    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 120 120');
    svg.setAttribute('class', 'donut');
    svg.setAttribute('role', 'group');
    svg.setAttribute('aria-label', '情绪构成：' + list.map(function (e) {
      return e + ' ' + (c[e] || 0) + ' 颗';
    }).join('，'));

    var R = 48, CIRC = 2 * Math.PI * R;
    var track = document.createElementNS(svgNS, 'circle');
    track.setAttribute('cx', '60'); track.setAttribute('cy', '60'); track.setAttribute('r', String(R));
    track.setAttribute('class', 'donut-track');
    svg.appendChild(track);

    var offset = 0;
    var centerTitle = UI.el('strong', 'donut-num', String(total));
    var centerSub = UI.el('span', 'donut-sub', '颗泡泡');

    if (total > 0) {
      list.forEach(function (e) {
        var n = c[e] || 0;
        if (!n) return;
        var frac = n / total;
        var seg = document.createElementNS(svgNS, 'circle');
        seg.setAttribute('cx', '60'); seg.setAttribute('cy', '60'); seg.setAttribute('r', String(R));
        seg.setAttribute('class', 'donut-seg');
        seg.setAttribute('stroke-dasharray', '0 ' + CIRC);
        seg.setAttribute('stroke-dashoffset', String(-offset * CIRC));
        seg.style.setProperty('--accent', colorOf(e));
        seg.setAttribute('tabindex', '0');
        seg.setAttribute('role', 'img');
        seg.setAttribute('aria-label', e + ' ' + n + ' 颗，占 ' + Math.round(frac * 100) + '%');
        svg.appendChild(seg);

        var dash = Math.max(0, frac * CIRC - 1.5);   // 留 1.5 的缝隙当分隔线
        requestAnimationFrame(function () {
          seg.setAttribute('stroke-dasharray', dash + ' ' + (CIRC - dash));
        });

        function focusSeg() {
          centerTitle.textContent = String(n);
          centerSub.textContent = e + ' · ' + Math.round(frac * 100) + '%';
          svg.classList.add('has-focus');
          seg.classList.add('is-active');
        }
        function blurSeg() {
          centerTitle.textContent = String(total);
          centerSub.textContent = '颗泡泡';
          svg.classList.remove('has-focus');
          seg.classList.remove('is-active');
        }
        seg.addEventListener('pointerenter', focusSeg);
        seg.addEventListener('pointerleave', blurSeg);
        seg.addEventListener('focus', focusSeg);
        seg.addEventListener('blur', blurSeg);

        offset += frac;
      });
    }

    wrap.appendChild(svg);
    var center = UI.el('div', 'donut-center');
    center.appendChild(centerTitle);
    center.appendChild(centerSub);
    wrap.appendChild(center);
    host.appendChild(wrap);

    // 图例：点击筛选，同时作用于柱状图与环形图
    emotions().forEach(function (e) {
      var n = c[e] || 0;
      var pct = total ? Math.round(n / total * 100) : 0;
      var btn = UI.el('button', 'legend-item');
      btn.type = 'button';
      btn.setAttribute('aria-pressed', hidden[e] ? 'false' : 'true');
      if (hidden[e]) btn.classList.add('is-off');
      var dot = UI.el('i', 'dot');
      accent(dot, e);
      btn.appendChild(dot);
      btn.appendChild(UI.el('span', 'legend-name', e));
      btn.appendChild(UI.el('b', 'legend-val', String(n)));
      // 被关掉的情绪不参与当前占比，显示破折号而不是一个会误导的百分比
      btn.appendChild(UI.el('span', 'legend-pct', hidden[e] ? '—' : pct + '%'));
      btn.addEventListener('click', function () {
        hidden[e] = !hidden[e];
        renderAll();
      });
      legendHost.appendChild(btn);
    });
  }

  /* ---------- 24 小时热力 ---------- */
  function renderHours(host) {
    host.innerHTML = '';
    var hs = hours();
    var max = Math.max.apply(null, hs);
    var wrap = UI.el('div', 'heat');
    wrap.setAttribute('role', 'img');
    wrap.setAttribute('aria-label', '24 小时发布分布，最活跃时段 ' + hs.indexOf(max) + ' 点');

    var tip = UI.el('div', 'chart-tip');
    tip.hidden = true;

    hs.forEach(function (n, h) {
      var cell = UI.el('button', 'heat-cell');
      cell.type = 'button';
      cell.setAttribute('aria-label', h + ' 点，' + n + ' 颗');
      var bar = UI.el('span', 'heat-bar');
      var hgt = max ? Math.max(3, Math.round(n / max * 56)) : 3;
      bar.style.height = '0px';
      if (!n) bar.classList.add('is-zero');
      cell.appendChild(bar);
      wrap.appendChild(cell);
      requestAnimationFrame(function () { bar.style.height = hgt + 'px'; });

      cell.addEventListener('pointerenter', function () {
        tip.hidden = false;
        tip.innerHTML = '';
        tip.appendChild(UI.el('strong', '', pad(h) + ':00 – ' + pad((h + 1) % 24) + ':00'));
        tip.appendChild(UI.el('span', 'chart-tip-total', n + ' 颗泡泡'));
      });
      cell.addEventListener('pointerleave', function () { tip.hidden = true; });
      cell.addEventListener('focus', function () {
        tip.hidden = false;
        tip.innerHTML = '';
        tip.appendChild(UI.el('strong', '', pad(h) + ':00 – ' + pad((h + 1) % 24) + ':00'));
        tip.appendChild(UI.el('span', 'chart-tip-total', n + ' 颗泡泡'));
      });
      cell.addEventListener('blur', function () { tip.hidden = true; });
    });

    host.appendChild(wrap);
    var axis = UI.el('div', 'heat-axis');
    ['00', '06', '12', '18', '23'].forEach(function (t) {
      axis.appendChild(UI.el('span', '', t));
    });
    host.appendChild(axis);
    host.appendChild(tip);
  }

  /* ---------- 洞察结论 ---------- */
  function renderNotes(host) {
    host.innerHTML = '';
    var c = counts();
    var list = emotions().filter(function (e) { return !hidden[e]; });
    var total = list.reduce(function (a, e) { return a + (c[e] || 0); }, 0);
    var notes = [];

    if (!total) {
      notes.push({ icon: '🌱', text: '还没有足够的泡泡，先发布一颗，洞察会慢慢长出来。' });
    } else {
      var top = list.slice().sort(function (a, b) { return (c[b] || 0) - (c[a] || 0); })[0];
      if (top && c[top]) {
        notes.push({
          icon: icons()[top] || '💭',
          text: '「' + top + '」占了 ' + Math.round(c[top] / total * 100) + '%，是这段时间的主色调。'
        });
      }
      var hs = hours();
      var peak = hs.indexOf(Math.max.apply(null, hs));
      if (Math.max.apply(null, hs) > 0) {
        notes.push({
          icon: '🕐',
          text: peak < 5 ? '凌晨 ' + peak + ' 点还有人在写，深夜的情绪最需要一个出口。'
            : (peak < 12 ? '上午 ' + peak + ' 点是表达欲的高峰。'
              : (peak < 18 ? '下午 ' + peak + ' 点最容易冒出想说的话。'
                : '晚上 ' + peak + ' 点最活跃，忙完一天才轮到自己。'))
        });
      }
      var delta = weekDelta();
      if (delta.cur || delta.prev) {
        var diff = delta.cur - delta.prev;
        notes.push({
          icon: diff >= 0 ? '📈' : '📉',
          text: diff === 0 ? '本周和上周的发布量持平，节奏很稳。'
            : (diff > 0 ? '本周比上周多 ' + diff + ' 颗，这里正在变热闹。'
              : '本周比上周少 ' + Math.abs(diff) + ' 颗，安静了一些。')
        });
      }
      var bucket = series().slice().sort(function (a, b) { return b.total - a.total; })[0];
      if (bucket && bucket.total) {
        notes.push({
          icon: '📅',
          text: (bucket.date.getMonth() + 1) + ' 月 ' + bucket.date.getDate() + ' 日是最近的高峰，一天出现了 ' + bucket.total + ' 颗泡泡。'
        });
      }
    }

    notes.slice(0, 4).forEach(function (n) {
      var li = UI.el('li', 'insight-note');
      var ico = UI.el('span', 'note-ico', n.icon);
      ico.setAttribute('aria-hidden', 'true');
      li.appendChild(ico);
      li.appendChild(UI.el('span', '', n.text));
      host.appendChild(li);
    });
  }

  /* ---------- 渲染 ---------- */
  function renderAll() {
    if (!data) return;
    var body = root.querySelector('.ins-body');
    body.innerHTML = '';

    var stats = UI.el('div', 'stat-grid');
    var trendCard = UI.el('section', 'chart-card');
    var trendHead = UI.el('header', 'chart-head');
    trendHead.appendChild(UI.el('h3', '', '发布趋势'));
    trendHead.appendChild(UI.el('span', 'chart-sub', '最近 ' + range + ' 天 · 按天'));
    trendCard.appendChild(trendHead);
    var trendHost = UI.el('div', 'chart-host');
    trendCard.appendChild(trendHost);

    var donutCard = UI.el('section', 'chart-card');
    var donutHead = UI.el('header', 'chart-head');
    donutHead.appendChild(UI.el('h3', '', '情绪构成'));
    donutHead.appendChild(UI.el('span', 'chart-sub', '点击图例可筛选'));
    donutCard.appendChild(donutHead);
    var donutHost = UI.el('div', 'donut-host');
    var legendHost = UI.el('div', 'legend');
    donutCard.appendChild(donutHost);
    donutCard.appendChild(legendHost);

    var hoursCard = UI.el('section', 'chart-card');
    var hoursHead = UI.el('header', 'chart-head');
    hoursHead.appendChild(UI.el('h3', '', '一天中的活跃时段'));
    hoursHead.appendChild(UI.el('span', 'chart-sub', '全部时间'));
    hoursCard.appendChild(hoursHead);
    var hoursHost = UI.el('div', 'chart-host');
    hoursCard.appendChild(hoursHost);

    var notesCard = UI.el('section', 'chart-card');
    var notesHead = UI.el('header', 'chart-head');
    notesHead.appendChild(UI.el('h3', '', '发现'));
    notesCard.appendChild(notesHead);
    var notesHost = UI.el('ul', 'insight-notes');
    notesCard.appendChild(notesHost);

    var grid1 = UI.el('div', 'ins-grid');
    grid1.appendChild(trendCard);
    grid1.appendChild(donutCard);
    var grid2 = UI.el('div', 'ins-grid');
    grid2.appendChild(hoursCard);
    grid2.appendChild(notesCard);

    body.appendChild(stats);
    body.appendChild(grid1);
    body.appendChild(grid2);

    renderStats(stats);
    renderTrend(trendHost);
    renderDonut(donutHost, legendHost);
    renderHours(hoursHost);
    renderNotes(notesHost);
  }

  function renderError(message) {
    var body = root.querySelector('.ins-body');
    body.innerHTML = '';
    var box = UI.el('div', 'ins-error');
    box.appendChild(UI.el('span', 'ins-error-ico', '📡'));
    box.appendChild(UI.el('p', '', message || '数据暂时取不到'));
    var retry = UI.el('button', 'btn-ghost', '重试');
    retry.type = 'button';
    retry.addEventListener('click', function () { load(true); });
    box.appendChild(retry);
    body.appendChild(box);
  }

  /* ---------- 数据 ---------- */
  function load(force) {
    if (!root) return;
    if (!force && data && Date.now() - fetchedAt < CACHE_MS) {
      renderAll();
      return;
    }
    skeleton();
    fetch('/api/insights', { headers: { 'Accept': 'application/json' } })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (json) {
        data = json;
        fetchedAt = Date.now();
        renderAll();
      })
      .catch(function () {
        renderError('数据暂时取不到，检查一下网络再试');
      });
  }

  /* ---------- 开关 ---------- */
  function open() {
    if (!root) return;
    if (!root.classList.contains('open')) {
      root.classList.add('open');
      root.setAttribute('aria-hidden', 'false');
      if (!locked) { locked = true; UI.lockScroll(true); }
      trapHandle = UI.trap(panel, {
        initial: '[data-insights-close]',
        onEscape: function () { close(); }
      });
      load(false);
    }
  }

  function close() {
    if (!root) return;
    root.classList.remove('open');
    root.setAttribute('aria-hidden', 'true');
    if (locked) { locked = false; UI.lockScroll(false); }
    if (trapHandle) { trapHandle.release(); trapHandle = null; }
  }

  function bind() {
    root = document.getElementById('insights-modal');
    if (!root) return;
    panel = root.querySelector('.modal-card');

    root.querySelectorAll('[data-insights-close]').forEach(function (n) {
      n.addEventListener('click', close);
    });
    root.addEventListener('click', function (e) {
      if (e.target === root) close();
    });
    document.querySelectorAll('[data-insights-open]').forEach(function (n) {
      n.addEventListener('click', function (e) { e.preventDefault(); open(); });
    });

    root.querySelectorAll('.seg button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var next = parseInt(btn.getAttribute('data-range'), 10);
        if (next === range) return;
        range = next;
        root.querySelectorAll('.seg button').forEach(function (b) {
          b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
        });
        if (data) renderAll();
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();

  return { open: open, close: close };
})();
