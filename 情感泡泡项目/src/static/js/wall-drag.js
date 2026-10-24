/* ===== wall-drag.js — 泡泡墙的拖拽重排 + 快捷菜单 =====
   拖拽用「占位符 + FLIP」方案：
   1. 按下后拖动超过阈值（鼠标 6px / 触摸长按 320ms），把泡泡改成 position:fixed，
      同时在原位插入一个等大的占位符——布局不塌，其他泡泡不跳动。
   2. 指针移动时，把占位符插到最近的槽位；其余泡泡用 FLIP 从旧位置平滑过渡到新位置
      （先量旧 rect，改 DOM，再量新 rect，反向位移后动画归零）。
      尺寸不一的网格也能精确对齐，这是纯 transform 排序做不到的。
   3. 松手时把泡泡放回占位符位置，从 fixed 的视觉位置动画落回槽位。

   顺序按用户存在 localStorage；轮询刷新后由 apply() 重新套用。
   键盘：聚焦泡泡后 Alt + 方向键 / Home / End 可重排，改动会播报给读屏。
*/
window.WallOrder = (function () {
  'use strict';

  var wall = null;
  var drag = null;
  var suppressClick = false;
  var liveRegion = null;
  var LONG_PRESS = 320;
  var MOUSE_THRESHOLD = 6;

  function key() {
    var user = (window.EB && window.EB.user) || 'anon';
    return 'eb-wall-order:' + user;
  }

  function load() {
    var v = UI.store.get(key(), []);
    return Array.isArray(v) ? v : [];
  }

  function save(ids) { UI.store.set(key(), ids); }

  function soaps() {
    return Array.prototype.filter.call(wall.children, function (n) {
      return n.classList && n.classList.contains('soap');
    });
  }

  function ids() {
    return soaps().map(function (n) { return n.getAttribute('data-bubble-id'); });
  }

  function announce(text) {
    if (!liveRegion) {
      liveRegion = UI.el('div', 'sr-only');
      liveRegion.setAttribute('aria-live', 'polite');
      document.body.appendChild(liveRegion);
    }
    liveRegion.textContent = '';
    setTimeout(function () { liveRegion.textContent = text; }, 30);
  }

  /* ---------- 顺序持久化 ---------- */
  function apply() {
    if (!wall) return;
    var saved = load();
    if (!saved.length) return;
    var nodes = soaps();
    if (nodes.length < 2) return;

    var byId = {};
    nodes.forEach(function (n) { byId[n.getAttribute('data-bubble-id')] = n; });

    var ordered = [];
    // 新出现的泡泡（不在存档里）保持服务端顺序排在前面——最新发布的最先看到
    nodes.forEach(function (n) {
      if (saved.indexOf(n.getAttribute('data-bubble-id')) < 0) ordered.push(n);
    });
    saved.forEach(function (id) { if (byId[id]) ordered.push(byId[id]); });

    var changed = ordered.some(function (n, i) { return n !== nodes[i]; });
    if (!changed) return;
    ordered.forEach(function (n) { wall.appendChild(n); });
  }

  function reset() {
    if (!wall) return false;
    UI.store.del(key());
    window.dispatchEvent(new CustomEvent('wall:refresh'));
    return true;
  }

  /* ---------- FLIP ---------- */
  function snapshot() {
    var map = new Map();
    soaps().forEach(function (n) {
      if (drag && n === drag.node) return;
      map.set(n, n.getBoundingClientRect());
    });
    return map;
  }

  function flip(before) {
    if (UI.reduced()) return;
    soaps().forEach(function (n) {
      if (drag && n === drag.node) return;
      var old = before.get(n);
      if (!old) return;
      var now = n.getBoundingClientRect();
      var dx = old.left - now.left;
      var dy = old.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      n.animate(
        [{ transform: 'translate(' + dx + 'px,' + dy + 'px)' }, { transform: 'translate(0,0)' }],
        { duration: 240, easing: 'cubic-bezier(.2,.8,.2,1)' }
      );
    });
  }

  /* 拖拽期间缓存所有槽位的矩形：每次 pointermove 只做距离比较，
     不再逐个 getBoundingClientRect（87 个泡泡时那是明显的掉帧源）。 */
  function captureRects() {
    drag.rects = soaps().filter(function (n) { return n !== drag.node; }).map(function (n) {
      var r = n.getBoundingClientRect();
      return { node: n, left: r.left, top: r.top, w: r.width, h: r.height };
    });
  }

  /* ---------- 拖拽 ---------- */
  function startDrag(e) {
    var node = drag.node;
    var rect = node.getBoundingClientRect();
    drag.started = true;
    drag.rect = rect;
    drag.grabX = e.clientX - rect.left;
    drag.grabY = e.clientY - rect.top;

    // 清掉 hover 留下的 GSAP transform，否则 fixed 定位会带着缩放/歪头
    if (window.gsap) {
      gsap.killTweensOf(node);
      gsap.set(node, { clearProps: 'transform' });
    }

    var slot = document.createElement('div');
    slot.className = 'soap-slot';
    slot.style.width = rect.width + 'px';
    slot.style.height = rect.height + 'px';
    node.parentNode.insertBefore(slot, node);
    drag.slot = slot;

    node.classList.add('is-lifted');
    node.style.position = 'fixed';
    node.style.left = rect.left + 'px';
    node.style.top = rect.top + 'px';
    node.style.width = rect.width + 'px';
    node.style.height = rect.height + 'px';
    node.style.margin = '0';
    node.style.zIndex = '70';
    node.style.transform = 'scale(1.05)';
    wall.classList.add('is-reordering');
    captureRects();
    if (navigator.vibrate) { try { navigator.vibrate(8); } catch (err) {} }
  }

  function moveDrag(e) {
    var node = drag.node;
    node.style.left = (e.clientX - drag.grabX) + 'px';
    node.style.top = (e.clientY - drag.grabY) + 'px';

    // 以指针为中心，找最近的槽位（用缓存矩形，避免每次移动都强制布局）
    var target = null, best = Infinity;
    drag.rects.forEach(function (r) {
      var dx = (r.left + r.w / 2) - e.clientX;
      var dy = (r.top + r.h / 2) - e.clientY;
      var d = dx * dx + dy * dy;
      if (d < best) { best = d; target = r; }
    });
    if (!target) return;

    var after = (e.clientX - (target.left + target.w / 2)) > 0;
    var ref = after ? target.node.nextSibling : target.node;
    if (ref === drag.slot) return;                 // 位置没变，别白跑 FLIP
    var before = snapshot();
    drag.slot.parentNode.insertBefore(drag.slot, ref);
    flip(before);
    captureRects();
    drag.moved = true;
  }

  /* 只清拖拽写进去的那几个属性。
     绝不能 cssText='' —— 泡泡的尺寸/膜色全在同一个 style 属性的自定义变量里。 */
  function resetNodeStyle(node) {
    node.style.position = '';
    node.style.left = '';
    node.style.top = '';
    node.style.width = '';
    node.style.height = '';
    node.style.margin = '';
    node.style.zIndex = '';
    node.style.transform = '';
  }

  function endDrag(cancelled) {
    var node = drag.node, slot = drag.slot;
    wall.classList.remove('is-reordering');
    node.classList.remove('is-lifted');

    if (cancelled) {
      // 取消（指针被系统抢走）：回到原位，不留痕迹
      slot.parentNode.insertBefore(node, slot);
      slot.remove();
      resetNodeStyle(node);
      drag = null;
      return;
    }

    var from = node.getBoundingClientRect();
    slot.parentNode.insertBefore(node, slot);
    slot.remove();
    resetNodeStyle(node);

    var to = node.getBoundingClientRect();
    if (!UI.reduced()) {
      node.animate(
        [{ transform: 'translate(' + (from.left - to.left) + 'px,' + (from.top - to.top) + 'px) scale(1.04)' },
         { transform: 'translate(0,0) scale(1)' }],
        { duration: 260, easing: 'cubic-bezier(.2,.8,.2,1)' }
      );
    }
    if (drag.moved) {
      save(ids());
      announce('已移动到第 ' + (ids().indexOf(node.getAttribute('data-bubble-id')) + 1) + ' 位');
    }
    drag = null;
  }

  function onPointerDown(e) {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    var node = e.target.closest('.soap');
    if (!node || !wall.contains(node)) return;
    if (drag) return;

    drag = {
      node: node,
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      started: false,
      moved: false,
      touch: e.pointerType !== 'mouse',
      timer: 0
    };
    node.setPointerCapture(e.pointerId);

    if (drag.touch) {
      // 触摸：长按才进入拖拽，避免和页面滚动打架
      drag.timer = setTimeout(function () {
        if (drag && !drag.started) startDrag({ clientX: drag.startX, clientY: drag.startY });
      }, LONG_PRESS);
    }
  }

  function onPointerMove(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    var dx = e.clientX - drag.startX;
    var dy = e.clientY - drag.startY;

    if (!drag.started) {
      if (drag.touch) {
        // 长按前先动了：说明是在滚动，放弃这次手势
        if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
          clearTimeout(drag.timer);
          drag = null;
        }
        return;
      }
      if (Math.abs(dx) < MOUSE_THRESHOLD && Math.abs(dy) < MOUSE_THRESHOLD) return;
      startDrag(e);
    }
    e.preventDefault();
    moveDrag(e);
  }

  function onPointerUp(e) {
    if (!drag || (e.pointerId != null && e.pointerId !== drag.pointerId)) return;
    clearTimeout(drag.timer);
    var wasDragging = drag.started;
    if (wasDragging) {
      // 拖过之后的 click 不能再去开侧栏
      suppressClick = true;
      setTimeout(function () { suppressClick = false; }, 0);
      endDrag(false);
    } else {
      drag = null;
    }
  }

  function onPointerCancel() {
    if (!drag) return;
    clearTimeout(drag.timer);
    if (drag.started) endDrag(true);
    else drag = null;
  }

  /* ---------- 键盘重排 ---------- */
  function moveByKeyboard(node, delta) {
    var list = soaps();
    var i = list.indexOf(node);
    if (i < 0) return;
    var j = Math.max(0, Math.min(list.length - 1, i + delta));
    if (i === j) return;
    var before = snapshot();
    var ref = delta > 0 ? list[j].nextSibling : list[j];
    wall.insertBefore(node, ref);
    flip(before);
    save(ids());
    announce('已移动到第 ' + (j + 1) + ' 位，共 ' + list.length + ' 个泡泡');
  }

  function onKeyDown(e) {
    var node = e.target.closest && e.target.closest('.soap');
    if (!node) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      node.click();
      return;
    }
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault();
      openMenuAt(node.getBoundingClientRect().left + 24, node.getBoundingClientRect().top + 24, node);
      return;
    }
    if (!e.altKey) return;
    var map = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };
    if (map[e.key] != null) {
      e.preventDefault();
      moveByKeyboard(node, map[e.key]);
    } else if (e.key === 'Home') {
      e.preventDefault(); moveByKeyboard(node, -9999);
    } else if (e.key === 'End') {
      e.preventDefault(); moveByKeyboard(node, 9999);
    }
  }

  /* ---------- 快捷菜单 ---------- */
  var menu = null;
  var menuOffEsc = null;
  var menuReturnFocus = null;

  function closeMenu() {
    if (!menu) return;
    menu.remove();
    menu = null;
    if (menuOffEsc) { menuOffEsc(); menuOffEsc = null; }
    document.removeEventListener('pointerdown', onDocDown, true);
    window.removeEventListener('scroll', closeMenu, true);
    if (menuReturnFocus && document.contains(menuReturnFocus)) {
      try { menuReturnFocus.focus({ preventScroll: true }); } catch (e) {}
    }
    menuReturnFocus = null;
  }

  function onDocDown(e) {
    if (menu && !menu.contains(e.target)) closeMenu();
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy') ? resolve() : reject(); }
      catch (e) { reject(e); }
      finally { ta.remove(); }
    });
  }

  function openMenuAt(x, y, node) {
    closeMenu();
    menuReturnFocus = document.activeElement;
    var id = node.getAttribute('data-bubble-id');
    var content = node.getAttribute('data-content') || '';
    var emotion = node.getAttribute('data-emotion') || '';
    var icons = (window.EB && window.EB.icons) || {};

    menu = UI.el('div', 'ctx-menu');
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', '泡泡快捷操作');

    var entries = [
      {
        icon: '💬', label: '打开对话',
        run: function () { if (window.openSidebar) window.openSidebar('/partial/panel/bubble/' + id); }
      },
      {
        icon: icons[emotion] || '🔎', label: '只看「' + emotion + '」',
        run: function () { location.href = '/?emotion=' + encodeURIComponent(emotion); }
      },
      {
        icon: '📋', label: '复制内容',
        run: function () {
          copyText(content).then(function () {
            UI.toast('内容已复制', { type: 'success', duration: 1800 });
          }).catch(function () {
            UI.toast('复制失败，请手动选择文本', { type: 'error' });
          });
        }
      }
    ];

    entries.forEach(function (entry, i) {
      var item = UI.el('button', 'ctx-item');
      item.type = 'button';
      item.setAttribute('role', 'menuitem');
      item.tabIndex = i === 0 ? 0 : -1;
      item.appendChild(UI.el('span', 'ctx-ico', entry.icon));
      item.appendChild(UI.el('span', 'ctx-label', entry.label));
      item.addEventListener('click', function () {
        closeMenu();
        entry.run();
      });
      menu.appendChild(item);
    });

    menu.style.left = '0px';
    menu.style.top = '0px';
    menu.style.visibility = 'hidden';
    document.body.appendChild(menu);

    // 贴边翻转：菜单不出视口
    var mr = menu.getBoundingClientRect();
    var left = Math.min(x, window.innerWidth - mr.width - 12);
    var top = Math.min(y, window.innerHeight - mr.height - 12);
    menu.style.left = Math.max(12, left) + 'px';
    menu.style.top = Math.max(12, top) + 'px';
    menu.style.visibility = '';

    // 闭包里用局部引用：Esc 的捕获监听会先把全局 menu 置空，
    // 事件继续传到菜单自身时若还读全局变量就会空指针。
    var menuEl = menu;

    menuEl.addEventListener('keydown', function (e) {
      if (!menuEl.isConnected) return;
      var list = Array.prototype.slice.call(menuEl.querySelectorAll('.ctx-item'));
      var i = list.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') {
        e.preventDefault(); list[(i + 1) % list.length].focus();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault(); list[(i - 1 + list.length) % list.length].focus();
      }
    });

    menuOffEsc = UI.onEscape(closeMenu);
    document.addEventListener('pointerdown', onDocDown, true);
    window.addEventListener('scroll', closeMenu, true);
    requestAnimationFrame(function () {
      if (!menuEl.isConnected) return;
      var first = menuEl.querySelector('.ctx-item');
      if (first) first.focus({ preventScroll: true });
    });
  }

  function onContextMenu(e) {
    var node = e.target.closest('.soap');
    if (!node) return;
    e.preventDefault();
    openMenuAt(e.clientX, e.clientY, node);
  }

  /* ---------- 初始化 ---------- */
  function init() {
    wall = document.getElementById('bubble-wall');
    if (!wall) return;

    wall.addEventListener('pointerdown', onPointerDown);
    wall.addEventListener('pointermove', onPointerMove, { passive: false });
    wall.addEventListener('pointerup', onPointerUp);
    wall.addEventListener('pointercancel', onPointerCancel);
    // 触摸拖拽时浏览器默认会把手势解释成滚动；非被动监听才能拦住
    wall.addEventListener('touchmove', function (e) {
      if (drag && drag.started) e.preventDefault();
    }, { passive: false });
    wall.addEventListener('keydown', onKeyDown);
    wall.addEventListener('contextmenu', onContextMenu);
    // 捕获阶段截住拖拽结束后的那次 click
    wall.addEventListener('click', function (e) {
      if (suppressClick) {
        e.preventDefault();
        e.stopPropagation();
        suppressClick = false;
      }
    }, true);

    apply();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return {
    apply: apply,
    reset: reset,
    isBusy: function () { return !!(drag && drag.started); },
    closeMenu: closeMenu
  };
})();
