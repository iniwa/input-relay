# 未完了タスク / 既知の課題

## #9 プレビュー描画の共通化（実装途中）

### 現状
- `receiver/shared_render.js` を新規作成し、`escapeHtml` / `renderLabelHtml` / `renderHistory` を `SharedRender` 名前空間で提供済み
- ただし `receiver/overlay.html` と `receiver/config_gui.html` からは未参照
- 両 HTML には `escapeHtml` が重複定義されたまま残っている

### 動作影響
- なし（XSS 対策は各ファイル内の重複定義で機能している）
- コード重複のみの問題

### 残タスク
**最小統合案（低リスク・推奨）**
1. `overlay.html` と `config_gui.html` の `<head>` に `<script src="/shared_render.js"></script>` を追加
2. 両ファイル内の重複 `escapeHtml` 定義を削除し、`SharedRender.escapeHtml` を参照するよう置換
3. リモート PC 環境では UI テスト不可のため、Main/Sub PC で OBS ブラウザソースと config GUI の動作確認が必要

**本格統合案（高リスク・保留）**
- プレビュー描画ロジック（約 900 行規模）自体を `shared_render.js` に寄せる
- UI 回帰テストが必要で、リモート PC では検証不可のため保留

### 参考コミット
- `dce08c4` 改善リスト全般の実装 / モジュール分割と堅牢化
