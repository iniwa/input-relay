// overlay.html と config_gui.html で共用するレンダリング補助。
// LAN 経由で書き換えられる config は外部入力扱い、innerHTML に入れる前に
// 必ず escapeHtml を通す。

(function (global) {
  'use strict';

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // 改行を <br> に、それ以外は HTML エスケープ。ラベル表示で共通利用する。
  function renderLabelHtml(label) {
    return escapeHtml(label).replace(/\\n/g, '<br>');
  }

  // 履歴 DOM 更新。entries は配列、flip=true で逆順。
  function renderHistory(container, entries, flip, entryClass) {
    const arr = flip ? entries.slice().reverse() : entries;
    const cls = entryClass || 'history-entry';
    container.innerHTML = arr
      .map(function (e) { return '<div class="' + cls + '">' + escapeHtml(e) + '</div>'; })
      .join('');
  }

  global.SharedRender = {
    escapeHtml: escapeHtml,
    renderLabelHtml: renderLabelHtml,
    renderHistory: renderHistory,
  };
})(typeof window !== 'undefined' ? window : this);
