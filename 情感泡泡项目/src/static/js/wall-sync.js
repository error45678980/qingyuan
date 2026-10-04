/* ===== wall-sync.js — keyed diff 增量同步 =====
   轮询拿到新 HTML 后，按 data-bubble-id / data-msg-id 复用节点而不是全量
   innerHTML——节点不被销毁，GSAP 动画对象就不会悬空。

   契约：
   1. 新增 key  → 插入新节点，hooks.onEnter 播入场动画
   2. 共有 key  → 复用现有节点（动画继续跑）；内容真变了才换 innerHTML，
                  换之前先杀子元素上的动画，hooks.onRefresh 让调用方重建
   3. 消失 key  → 先 gsap.killTweensOf 再 hooks.onLeave（退场动画里自行 remove），
                  kill 必须在 remove 之前——无限循环的 tween 不杀会永久泄漏
   4. 无 key 的节点（如 .empty 空态）只在新片段也无 key 时整体替换
*/
window.WallSync = (function () {
  'use strict';

  function keyOf(node) {
    return node.getAttribute('data-bubble-id') || node.getAttribute('data-msg-id');
  }

  function parse(html) {
    var tmp = document.createElement('div');
    tmp.innerHTML = html;
    var map = new Map();
    Array.prototype.forEach.call(tmp.children, function (n) {
      var k = keyOf(n);
      if (k) map.set(k, n);
    });
    return map;
  }

  function killTree(node) {
    if (!window.gsap) return;
    gsap.killTweensOf(node);
    gsap.killTweensOf(node.querySelectorAll('*'));
  }

  /* container: 列表容器；html: 新片段；
     hooks: { onEnter(node,isFirst), onLeave(node), onRefresh(node), immutable }
     immutable=true 时共有节点完全不碰（消息/泡泡内容本就不可变；
     且聊天时间会被 localizeTimes 改写，比 innerHTML 必然不等） */
  function sync(container, html, hooks) {
    hooks = hooks || {};
    var immutable = !!hooks.immutable;
    var next = parse(html);

    // 空态/无 key 场景：整片替换（此时列表里没有动画中的节点）
    if (next.size === 0) {
      Array.prototype.slice.call(container.children).forEach(killTree);
      container.innerHTML = html;
      return 0;
    }
    // 新片段有 key：清掉容器里的无 key 节点（如"还没有泡泡"空态）
    Array.prototype.slice.call(container.children).forEach(function (c) {
      if (!keyOf(c)) c.remove();
    });

    // 1. 消失节点：先杀动画再退场
    Array.prototype.slice.call(container.children).forEach(function (cur) {
      var k = keyOf(cur);
      if (!k || next.has(k)) return;
      killTree(cur);
      if (hooks.onLeave) hooks.onLeave(cur);
      else cur.remove();
    });

    // 2. 按新顺序归位（从后往前插，anchor 是下一个兄弟）
    var keys = Array.from(next.keys());
    var anchor = null;
    for (var i = keys.length - 1; i >= 0; i--) {
      var k = keys[i];
      var fresh = next.get(k);
      var existing = null;
      Array.prototype.forEach.call(container.children, function (c) {
        if (keyOf(c) === k) existing = c;
      });
      if (existing) {
        if (!immutable && existing.innerHTML !== fresh.innerHTML) {
          // 内容变了：先杀子元素动画再换内脏，节点本身（含位置/状态）保留
          if (window.gsap) gsap.killTweensOf(existing.querySelectorAll('*'));
          existing.innerHTML = fresh.innerHTML;
          if (hooks.onRefresh) hooks.onRefresh(existing);
        }
        if (existing.nextElementSibling !== anchor) {
          container.insertBefore(existing, anchor);
        }
        anchor = existing;
      } else {
        container.insertBefore(fresh, anchor);
        if (hooks.onEnter) hooks.onEnter(fresh, i === 0);
        anchor = fresh;
      }
    }
    return next.size;
  }

  return { sync: sync };
})();
