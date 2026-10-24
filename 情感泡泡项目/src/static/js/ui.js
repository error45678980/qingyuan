/* ===== ui.js — 交互基础设施 =====
   三个小组件，被所有上层组件复用：
   1. Toast：可堆叠、悬停暂停、触摸滑动关闭；同一时刻最多 3 条。
   2. 焦点陷阱 + ESC 栈：侧栏 / 弹窗 / 命令面板叠在一起时，
      只有最上层响应 Esc，Tab 也不会跑到背后去。
   3. 小工具：localStorage 安全读写、reduced-motion 判定、DOM 构造。

   设计约束：不引入依赖，不写 innerHTML（消息文本可能来自用户输入）。
*/
window.UI = (function () {
  'use strict';

  var reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  function reduced() { return reduceMQ.matches; }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ==================== 存储 ==================== */
  var store = {
    get: function (key, fallback) {
      try {
        var raw = localStorage.getItem(key);
        return raw == null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    set: function (key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); return true; }
      catch (e) { return false; }          // 隐私模式/配额满：静默降级
    },
    del: function (key) {
      try { localStorage.removeItem(key); } catch (e) {}
    }
  };

  /* ==================== Toast ==================== */
  var stack = null;
  var MAX_VISIBLE = 3;
  var ICONS = { success: '✓', error: '!', info: '💡' };

  function ensureStack() {
    if (stack && document.body.contains(stack)) return stack;
    stack = el('div', 'toast-stack');
    stack.setAttribute('aria-live', 'polite');
    stack.setAttribute('aria-atomic', 'false');
    document.body.appendChild(stack);
    return stack;
  }

  function dismiss(node) {
    if (!node || node._closing) return;
    node._closing = true;
    clearTimeout(node._timer);
    node.classList.add('is-out');
    var done = function () { node.remove(); };
    if (reduced()) { done(); return; }
    node.addEventListener('animationend', done, { once: true });
    setTimeout(done, 320);               // 兜底：动画被禁用时也能收掉
  }

  function toast(message, opts) {
    opts = opts || {};
    var type = ICONS[opts.type] ? opts.type : 'info';
    var duration = opts.duration == null ? 3400 : opts.duration;
    var host = ensureStack();

    // 超过上限：先请走最老的一条（不打断当前这条的入场）
    var live = Array.prototype.filter.call(host.children, function (n) {
      return !n._closing;
    });
    if (live.length >= MAX_VISIBLE) dismiss(live[0]);

    var node = el('div', 'toast toast--' + type);
    node.setAttribute('role', 'status');

    var ico = el('span', 'toast-ico', ICONS[type]);
    ico.setAttribute('aria-hidden', 'true');

    var body = el('div', 'toast-body');
    body.appendChild(el('p', 'toast-msg', String(message)));
    if (opts.action && typeof opts.action.onClick === 'function') {
      var act = el('button', 'toast-act', opts.action.label || '撤销');
      act.type = 'button';
      act.addEventListener('click', function () {
        opts.action.onClick();
        dismiss(node);
      });
      body.appendChild(act);
    }

    var close = el('button', 'toast-x', '✕');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭提示');
    close.addEventListener('click', function () { dismiss(node); });

    var bar = el('span', 'toast-bar');
    bar.setAttribute('aria-hidden', 'true');

    node.appendChild(ico);
    node.appendChild(body);
    node.appendChild(close);
    node.appendChild(bar);
    host.appendChild(node);

    // —— 计时：悬停/聚焦暂停，离开后按剩余时间续跑 ——
    var remaining = duration;
    var startedAt = 0;
    function start() {
      if (duration <= 0) return;
      startedAt = Date.now();
      bar.style.animationDuration = remaining + 'ms';
      bar.classList.remove('is-paused');
      node._timer = setTimeout(function () { dismiss(node); }, remaining);
    }
    function pause() {
      if (duration <= 0 || !startedAt) return;
      clearTimeout(node._timer);
      remaining = Math.max(600, remaining - (Date.now() - startedAt));
      startedAt = 0;
      bar.classList.add('is-paused');
    }
    function resume() { if (!node._closing && !startedAt) start(); }
    node.addEventListener('pointerenter', pause);
    node.addEventListener('pointerleave', resume);
    node.addEventListener('focusin', pause);
    node.addEventListener('focusout', resume);
    start();

    // —— 触摸/鼠标横向滑动关闭 ——
    var sx = 0, sy = 0, dragging = false, dx = 0;
    node.addEventListener('pointerdown', function (e) {
      if (e.target.closest('button')) return;
      sx = e.clientX; sy = e.clientY; dragging = true; dx = 0;
      node.setPointerCapture(e.pointerId);
      pause();
    });
    node.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      dx = e.clientX - sx;
      if (Math.abs(dx) < 8 && Math.abs(e.clientY - sy) < 8) return;
      node.style.transform = 'translateX(' + dx + 'px)';
      node.style.opacity = String(Math.max(0.35, 1 - Math.abs(dx) / 220));
    });
    function endSwipe() {
      if (!dragging) return;
      dragging = false;
      node.style.transform = '';
      node.style.opacity = '';
      if (Math.abs(dx) > 70) dismiss(node);
      else resume();
    }
    node.addEventListener('pointerup', endSwipe);
    node.addEventListener('pointercancel', endSwipe);

    return { dismiss: function () { dismiss(node); } };
  }

  /* ==================== 焦点陷阱 + ESC 栈 ==================== */
  var FOCUSABLE = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  function focusables(root) {
    return Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (n) {
      if (n.hidden || n.getAttribute('aria-hidden') === 'true') return false;
      // offsetParent 为 null 通常是 display:none；fixed 元素的 offsetParent 也是 null，
      // 所以再用 getClientRects 兜一层
      return n.offsetParent !== null || n.getClientRects().length > 0;
    });
  }

  var escStack = [];
  function onEscape(fn) {
    escStack.push(fn);
    return function off() {
      var i = escStack.indexOf(fn);
      if (i >= 0) escStack.splice(i, 1);
    };
  }
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !escStack.length) return;
    var fn = escStack[escStack.length - 1];
    if (fn(e) !== false) e.preventDefault();
  }, true);

  /* 把焦点锁在 container 内；release() 时归还给打开它的那个元素。
     initial 可以是选择器或元素；缺省聚焦第一个可聚焦项。 */
  function trap(container, opts) {
    opts = opts || {};
    var previous = document.activeElement;
    var offEsc = opts.onEscape ? onEscape(opts.onEscape) : null;

    function onKeydown(e) {
      if (e.key !== 'Tab') return;
      var list = focusables(container);
      if (!list.length) { e.preventDefault(); return; }
      var first = list[0], last = list[list.length - 1];
      if (e.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    }
    container.addEventListener('keydown', onKeydown);

    var initial = opts.initial;
    if (typeof initial === 'string') initial = container.querySelector(initial);
    if (!initial) initial = focusables(container)[0];

    // 强制一次样式解析：容器通常刚从 visibility:hidden 切到可见，
    // 浏览器若还没重算样式，focus() 会静默失败（activeElement 仍是 body）。
    void container.offsetWidth;

    function focusInitial() {
      if (!initial || !initial.focus) return;
      try { initial.focus({ preventScroll: true }); } catch (e) {}
    }
    if (initial && initial.focus) {
      requestAnimationFrame(function () {
        focusInitial();
        // 还差一帧的情况（样式/过渡尚未提交）再补一次，不弹键盘、不滚动
        if (document.activeElement !== initial) {
          setTimeout(focusInitial, 60);
        }
      });
    }

    var released = false;
    return {
      release: function () {
        if (released) return;
        released = true;
        container.removeEventListener('keydown', onKeydown);
        if (offEsc) offEsc();
        if (previous && previous.focus && document.contains(previous)) {
          try { previous.focus({ preventScroll: true }); } catch (e) {}
        }
      }
    };
  }

  /* ==================== 滚动锁 ==================== */
  /* 引用计数：侧栏、发布弹窗、洞察面板、命令面板可能叠在一起，
     任何一个关闭都不该把别人的锁一起解掉。 */
  var scrollLocks = 0;
  function lockScroll(locked) {
    scrollLocks += locked ? 1 : -1;
    if (scrollLocks < 0) scrollLocks = 0;
    document.body.classList.toggle('modal-open', scrollLocks > 0);
  }

  /* ==================== 数字滚动 ==================== */
  /* 把 from → to 的整数在 duration 内缓动到目标；用于统计卡 */
  function countUp(node, to, duration) {
    if (reduced() || duration === 0) { node.textContent = String(to); return; }
    var from = parseInt(node.textContent, 10) || 0;
    var t0 = performance.now();
    duration = duration || 620;
    function frame(now) {
      var p = Math.min(1, (now - t0) / duration);
      var eased = 1 - Math.pow(1 - p, 3);
      node.textContent = String(Math.round(from + (to - from) * eased));
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function fmtTime(ts) {
    var d = new Date(ts);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  return {
    reduced: reduced,
    el: el,
    store: store,
    toast: toast,
    trap: trap,
    onEscape: onEscape,
    focusables: focusables,
    lockScroll: lockScroll,
    countUp: countUp,
    fmtTime: fmtTime
  };
})();
