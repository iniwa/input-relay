# プログラム改善チェックリスト

コードベースを調査して洗い出した改善候補の一覧
（初回調査: 2026-07-08 / 再調査: 2026-07-08 二巡目）。

**運用方法**: 着手したい項目にチェック `[x]` を入れる → Codex が handoff を作成し、
Claude Code（Sonnet 実務・auto モード）が実装する。handoff を挟むまでもない
小粒な項目は Claude Code に直接依頼してもよい。
実装完了した項目は「完了アーカイブ」へ移動する。

- 機能追加・未検証項目はこのファイルの対象外。
- 優先度: **高** = 常駐運用での安定性に直結 / **中** = 保守性・性能 / **低** = 任意。
- sender は Main PC 常駐・receiver は Sub PC 常駐のため、無制限に増える
  メモリ/キューと入力イベント経路の遅延が最優先の観点。

---

## 改善候補

- [ ] **【低】`sender/input_sender.py`（835行）の責務を分割する**
  - 現状: receiver への WS クライアント / monitor WS サーバー /
    HTTP config API（`SenderHTTPHandler`）/ キーボード・マウス listener 管理 /
    リモートモード制御が1ファイルに同居。
  - 対応案: sender/ 配下の既存分割（gamepad / raw_mouse / ll_mouse_hook /
    overlay_window）に合わせて HTTP API と monitor WS を別モジュールへ
    切り出す（挙動・ポート・API 不変）。

- [ ] **【低】standalone_capture のキー正規化・gamepad polling の重複を解消する**
  - 現状: `receiver/standalone_capture.py:24-57`
    （`_MODIFIER_MAP` / `_key_to_str` / `_get_vk`）は
    `sender/input_sender.py:131-141, 194-213, 269-278` とほぼ同一。
    同じく `standalone_capture.py:117-207` の gamepad polling は
    `sender/gamepad.py:124-218` の機能縮小コピー（コントローラ選択・
    再スキャン API なし）。片側だけ直す修正漏れの温床になっている。
  - 対応案: キー正規化を共有モジュールに抽出して両者から import する
    （挙動不変）。gamepad 側は `Gamepad` クラスの流用可否に配置の
    設計判断（sender/ と receiver/ の境界）が絡むため handoff 前に
    Codex 確認。
  - 制約: `.bat` 起動（sender / receiver / standalone）と
    別 PC デプロイ構成を壊さない。

- [ ] **【低】lint とテストの最低限を整備する**
  - 現状: ruff 設定なし・テスト0件（2026-07-08 時点、`tests/` なし）。
  - 対応案: ruff をデフォルト設定で通し、純ロジック部
    （イベント整形 `make_event` 系・プリセット CRUD）に最小テストを足す。
  - 制約: 実行時依存は追加しない。bat の自動インストール手順は変えない。

---

## 対象外と判断したもの（2026-07-08 調査メモ）

- `receiver/config_gui.html`（2495行）: ビルドなし単一ファイル GUI は
  この規模のツールでは意図的な構成のため分割候補にしない。
- 切断まわりの `except: pass`: シャットダウン時のベストエフォート解放で意図的。
- gamepad / raw mouse の 60Hz ポーリング: 変化時のみ送信・累積 flush 済みで
  CPU 負荷は問題なし。

---

## 完了アーカイブ

### 2026-07-09: receiver のブラウザ配信を per-client 送信にする
検証: `python -m py_compile receiver/input_server.py` OK、`git diff --check` OK。
`broadcast_to_browsers` を `asyncio.gather` の一括待ちから for ループでの
per-client 送信に変更。`_browser_lock` 配下でスナップショットした
クライアント一覧に対し、ロックを外して1件ずつ `send` し、失敗した
クライアントのみ `_browser_lock` 配下でまとめて discard する。
`sender_handler` 内で重複していたブラウザ向けファンアウト（`asyncio.gather`
の直書き）は `broadcast_to_browsers(msg)` 呼び出しに置き換え、実装を一本化。
メッセージペイロードは変更なし、リモコン注入 (`input_injector.replay_event`)
の呼び出し条件も従来通り。ルート・ポート・JSON形状の変更なし。

### 2026-07-09: receiver 静的ファイル配信のパストラバーサルを塞ぐ
検証: `python -m py_compile sender/*.py receiver/*.py` OK。
`OverlayHandler._resolve_static_path` を追加し、`(OVERLAY_DIR / path).resolve()`
が `OVERLAY_DIR.resolve()` 配下（`Path.is_relative_to`）でなければ 404 を返す
ように変更。`GET /../config/config.json` と `GET /C:/Windows/win.ini` が
404 になることを実挙動で確認（`is_relative_to` 判定のみで再現、要 Live 確認は Sub PC）。
`/overlay.html` `/shared_render.js` 等の正規静的配信・`/history` `/input`
`/mouse-trail` の overlay モードルートは挙動不変。

### 2026-07-09: sender の event_queue を receiver 切断中に溜め込まないようにする
検証: `python -m py_compile sender/*.py receiver/*.py` OK。
`event_queue` を `asyncio.Queue(maxsize=500)` に変更し、`_post_event` は
`ws_status == "connected"` のときのみ enqueue をスケジュールするように変更
（切断中はイベントを破棄）。満杯時は loop スレッド上の `_enqueue_event_on_loop`
で最古を drop してから最新を積む。remote_control トグルは元々 `_post_event`
を経由せず `ws_connection.send` / 接続確立時の直接送信のため、この変更の影響を受けない。

### 2026-07-08: 撤去済み toggleKey 設定キーの残骸を削除
検証: `python -m py_compile sender/*.py receiver/*.py` OK。コミット範囲: 53962e1。
- ✅ `toggleKey` を `_CONFIG_DEFAULTS` と `docs/api.md` から削除
  → 参照コード 0 件の死に設定を除去（既存 config に残っていても無視されるだけで無害）
