/* ===== motion.js — GSAP 交互动效层 =====
   职责划分：
   - 连续装饰动画（虹彩旋转 soap-spin / 涟漪 soap-ripple / 装饰泡泡 orn-float）
     保留在 CSS @keyframes——纯装饰、无限循环，CSS 原生处理最稳，
     且在 file:// / 无 JS 环境下依然安全（reduced-motion 由 CSS 兜底关掉）。
   - GSAP 只接管"有始有终"的交互：新泡泡飘落入场、hover 歪头、按压、
     泡泡退场、新消息入场。零无限 tween = 零泄漏面。

   依赖：gsap.min.js（vendor 本地），wall-sync.js 通过 hooks 调用 enter/leave。
*/
window.WallMotion = (function () {
  'use strict';

  var reduceQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  function reduced() { return reduceQuery.matches; }
  function ready() { return !!window.gsap && !reduced(); }

  /* 新泡泡：从上方飘落入场（一次性，落定后交还 CSS 控制） */
  function enter(node) {
    if (!ready()) return;
    gsap.from(node, {
      y: -40, opacity: 0, scale: .85,
      duration: .8, ease: 'power2.out',
      clearProps: 'transform,opacity'
    });
  }

  /* 消失泡泡：收缩淡出后移除节点。
     调用方（wall-sync）已先 killTweensOf，这里只做视觉退场。 */
  function leave(node) {
    if (!ready()) { node.remove(); return; }
    gsap.to(node, {
      scale: .3, opacity: 0, duration: .3, ease: 'power2.in',
      onComplete: function () { node.remove(); }
    });
  }

  /* 新聊天消息：自己发的从右滑入，对方从左滑入 */
  function msgEnter(node) {
    if (!ready()) return;
    var fromX = node.classList.contains('me') ? 12 : -12;
    gsap.from(node, {
      x: fromX, opacity: 0, duration: .28,
      ease: 'power2.out', clearProps: 'transform,opacity'
    });
  }

  /* hover 歪头 + 按压：事件委托挂在容器上，节点被 diff 复用/替换都不受影响 */
  function tiltOf(soap) {
    var n = parseInt(soap.getAttribute('data-bubble-id'), 36);
    return (isNaN(n) || n % 2) ? 6 : -6;
  }

  function initWall(wall) {
    if (!window.gsap || !wall || wall._motionBound) return;
    wall._motionBound = true;

    wall.addEventListener('mouseover', function (e) {
      if (reduced()) return;
      var s = e.target.closest('.soap');
      if (!s || (e.relatedTarget && s.contains(e.relatedTarget))) return;
      gsap.to(s, { scale: 1.06, rotation: tiltOf(s), duration: .32, ease: 'back.out(1.7)' });
    });
    wall.addEventListener('mouseout', function (e) {
      var s = e.target.closest('.soap');
      if (!s || (e.relatedTarget && s.contains(e.relatedTarget))) return;
      gsap.to(s, { scale: 1, rotation: 0, duration: .32, ease: 'power2.out' });
    });
    wall.addEventListener('mousedown', function (e) {
      if (reduced()) return;
      var s = e.target.closest('.soap');
      if (!s) return;
      gsap.to(s, { scale: .94, duration: .12, ease: 'power2.in' });
    });
    wall.addEventListener('mouseup', function (e) {
      var s = e.target.closest('.soap');
      if (!s) return;
      // 松手时鼠标大概率还在泡泡上：先回到 hover 态，mouseout 会收尾
      gsap.to(s, { scale: reduced() ? 1 : 1.06, rotation: reduced() ? 0 : tiltOf(s), duration: .18, ease: 'power2.out' });
    });
  }

  return { enter: enter, leave: leave, msgEnter: msgEnter, initWall: initWall };
})();
