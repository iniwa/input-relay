# 未完了タスク / 既知の課題

（現時点で未完了項目なし）

## 完了履歴

### #9 プレビュー描画の共通化（2026-04-13 完了）
- `overlay.html` / `config_gui.html` の `<head>` に `<script src="/shared_render.js"></script>` を追加
- 両ファイル内の重複 `escapeHtml` 定義を削除し、`SharedRender.escapeHtml` を参照
- ラベル改行処理（`\n` → `<br>`）も `SharedRender.renderLabelHtml` に統一
- Sub PC 上で standalone モードを起動し Playwright で検証:
  - `/shared_render.js` が 200 で配信されること
  - 両 HTML で `SharedRender` がロードされ、`escapeHtml === SharedRender.escapeHtml` であること
  - XSS ペイロード（`<script>`, `<img onerror=...>`）が正しくエスケープされること
  - `setLabelContent` / `pvSetLabel` が `A\n<b>` → `A<br>&lt;b&gt;` に変換すること
  - コンソールエラーなし（favicon 404 のみ）
- 参考コミット: `dce08c4` 改善リスト全般の実装
