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

- [ ] **【高】receiver 静的ファイル配信のパストラバーサルを塞ぐ**
  - 現状: `receiver/input_server.py:374-395` の `do_GET` は
    `urlparse(self.path).path.lstrip("/")` を `OVERLAY_DIR / path` にそのまま
    連結して `read_bytes()` する。`Path` の連結仕様により
    `GET /../config/config.json`（相対脱出）と `GET /C:/Windows/win.ini`
    （絶対パス — `Path / "C:/..."` は絶対側が勝つ）の両方が解決されることを
    実挙動で確認済み（2026-07-08）。ブラウザは `../` を正規化するが、
    raw HTTP クライアントからは Sub PC 上の任意ファイルを読み出せる。
  - 対応案: `(OVERLAY_DIR / path).resolve()` が
    `OVERLAY_DIR.resolve()` 配下（`is_relative_to`）であることを確認し、
    外れたら 404。正規パス（`/overlay.html`・`shared_render.js` 等の
    `receiver/` 直下配信と `/history` `/input` `/mouse-trail`）の挙動は不変。
  - 制約: LAN 内信頼ゾーン運用（認証なし）は既存設計のまま。
    ポート・ルーティングは変えない。

- [ ] **【高】sender の `event_queue` を receiver 切断中に溜め込まないようにする**
  - 現状: `sender/input_sender.py:216` の `event_queue` は上限なしの
    `asyncio.Queue` で、`_post_event`（同 :219-222）は接続状態を見ずに積む。
    receiver 切断中も入力キャプチャは動き続ける（pynput フック +
    raw mouse 60Hz flush `sender/raw_mouse.py:23` + gamepad 60Hz
    `sender/gamepad.py:20`）ため、Sub PC 停止中に Main PC を使い続けると
    イベントがメモリに無制限蓄積し、再接続時には溜まった古いイベントを
    一括送信してしまう。
  - 対応案: `ws_status != "connected"` のとき `_post_event` で捨てる、
    または `Queue(maxsize=N)` + 満杯時 drop-oldest。切断中のイベントは
    元々表示できないため、表示挙動は不変。
  - 制約: 接続中のイベント順序・remote_control トグルの伝達は変えない。

- [ ] **【低】receiver のブラウザ配信を per-client 送信にする**
  - 現状: `receiver/input_server.py:518-524` は全ブラウザクライアントへの
    `send` を `asyncio.gather` で完了待ちしてから次のイベントを処理する。
    1つの遅いクライアント（停止した OBS ブラウザソース等）が他クライアント
    への転送とリモコン注入（同 :527 以降）を巻き込んで遅延させる。
    standalone モードの `_standalone_broadcaster`（同 :564-568）も同じ
    `broadcast_to_browsers` 経由のため同根。
  - 対応案: sender 側 monitor 配信（`sender/input_sender.py:548` の
    「send 失敗クライアントを discard」パターン）と同様に、per-client で
    送信し滞留・切断クライアントを切り離す。

- [ ] **【低】`sender/input_sender.py`（835行）の責務を分割する**
  - 現状: receiver への WS クライアント / monitor WS サーバー /
    HTTP config API（`SenderHTTPHandler`）/ キーボード・マウス listener 管理 /
    リモートモード制御が1ファイルに同居。
  - 対応案: sender/ 配下の既存分割（gamepad / raw_mouse / ll_mouse_hook /
    overlay_window）に合わせて HTTP API と monitor WS を別モジュールへ
    切り出す（挙動・ポート・API 不変）。

- [ ] **【低】撤去済み `toggleKey` 設定キーの残骸を除去する**
  - 現状: F12 によるオーバーレイ表示モード循環切替は commit fd325f3
    （2026-03-18）で撤去済みだが、`sender/input_sender.py:55` の
    `_CONFIG_DEFAULTS` に `"toggleKey": "f12"` が残り、`docs/api.md` にも
    2箇所記載が残っている（コード全体で `toggleKey` を読む箇所は 0 件）。
    現在のモード切替は設定 GUI のボタン → `POST /api/mode-switch` のみ。
  - 対応案: デフォルトと api.md の記載から削除する（挙動不変。既存の
    `sender_config.json` にキーが残っていても `_merge_defaults` は
    追記しかしないため無害）。キー切替の再実装が必要なら別途 issue 化。

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

（なし）
