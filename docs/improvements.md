# プログラム改善チェックリスト

コードベースを調査して洗い出した改善候補の一覧
（初回調査: 2026-07-08 / 再調査: 2026-07-08、2026-07-11）。

**運用方法**: 着手したい項目にチェック `[x]` を入れる → Codex が handoff を作成し、
Claude Code（`claude -p --model sonnet --permission-mode auto` / Sonnet 5）が
実装する。handoff を挟むまでもない
小粒な項目は Claude Code に直接依頼してもよい。
実装完了した項目は「完了アーカイブ」へ移動する。

- 機能追加・未検証項目はこのファイルの対象外。
- 優先度: **高** = 常駐運用での安定性に直結 / **中** = 保守性・性能 / **低** = 任意。
- sender は Main PC 常駐・receiver は Sub PC 常駐のため、無制限に増える
  メモリ/キューと入力イベント経路の遅延が最優先の観点。

---

## 改善候補

### 1. 常駐キュー・入力ソース・WebSocket

- [ ] **【高】sender monitor WebSocket の queue と client send 待ちを有界化する**
  - 現状: `sender/monitor_ws.py:28` は `asyncio.Queue()` 無上限で、client send
    (`sender/monitor_ws.py:49-63`) に timeout がない。raw mouse は最大 62.5 event/s、
    gamepad は 6 軸変化時最大 360 event/s なので、1 slow client で最大約
    422.5 event/s（25,350/min）が無制限に蓄積し得る。
  - 対応案: queue を 500 件程度に制限し、loop 上で満杯時 oldest-drop、client 0 件時は
    即 drop、client ごとの send timeout 後に close/discard する。slow/healthy client と
    overflow の async test を追加する。
  - 制約: port 8083、JSON、healthy FIFO、receiver 切断中も local monitor 可能な挙動を維持。

- [ ] **【高】receiver browser fan-out を client 間・RC 注入から独立させる**
  - 現状: `broadcast_to_browsers` は client ごとに最大 1.0 秒を直列で待つ
    (`receiver/input_server.py:29,123-143`)。sender handler は broadcast 完了後に
    `replay_event` する (`receiver/input_server.py:543-551`) ため、stalled client N 件で
    key-down/up 注入が名目最大 N 秒遅れる。2026-07-09 の完了アーカイブにある
    「遅い client が他 client / RC 注入を遅らせない」という目的を満たしていない。
  - 対応案: RC の state 判定・注入を browser await より前に行い、browser send は
    client ごとの timeout/cleanup を保ったまま同時進行させる。1 stalled + 1 healthy
    client で healthy 配信と注入が待たないことをテストする。
  - 制約: client ごとの message 順、payload、failed client discard、RC 条件を維持。

- [ ] **【高】standalone の入力 queue を有界化する**
  - 現状: `receiver/input_server.py:578-588,604` は producer 無条件 `put_nowait`、
    consumer 1 coroutine、`asyncio.Queue()` 無上限。6 軸が変化する gamepad だけでも
    最大 360 event/s で、500 件相当を約 1.39 秒で超える。
  - 対応案: sender と同じ 500 件上限 + loop thread 上の oldest-drop/newest enqueue とし、
    overflow log を rate limit する。key down/up の overflow 時 state reset 方針を
    handoff で固定し、queue 上限テストを追加する。
  - 制約: standalone JSON、通常 FIFO、60Hz、keyboard/mouse/gamepad 対応を維持。

- [ ] **【高】Raw Input の偽 fallback と失敗経路の resource cleanup を直す**
  - 現状: `SetTimer` 失敗時に fallback と表示する (`sender/raw_mouse.py:206-207`) が、
    `flush()` は WM_TIMER 分岐 (`sender/raw_mouse.py:169-171`) だけで、poll loop
    (`sender/raw_mouse.py:217-224`) は timeout 後に flush しないため送信は 0 件/s になる。
    また `timeBeginPeriod(1)` 後の 3 early-return (`sender/raw_mouse.py:183-203`) は
    `timeEndPeriod` 等の finally (`sender/raw_mouse.py:225-234`) に入らない。
  - 対応案: timer 成否を保持し、失敗時は 16ms wake ごとに累積 delta を flush する。
    `timeBeginPeriod` 以後の全 resource acquisition を単一 `try/finally` に入れる。
  - 制約: 16ms throttle、delta 精度、Raw Input background capture、message shape を維持。

- [ ] **【高】共有 Gamepad を切断時 neutralize し、一過性例外から再開させる**
  - 現状: 物理切断・controller 切替時に 3 state buffer を release/neutral event なしで
    clear する (`input_common/gamepad.py:147-168,224-232`) ため表示が残る。`run()` は
    pygame init/scan/pump/getter の例外で終了 (`input_common/gamepad.py:106-126`) し、
    sender/standalone とも daemon thread の起動は各 1 回、retry は 0 回
    (`sender/input_sender.py:570-573`, `receiver/standalone_capture.py:85-93,109-110`)。
  - 対応案: clear 前に active button/hat/threshold axis の key-up と raw axis 0 を emit し、
    bounded backoff で pygame session を再初期化する。fake pygame/joystick test を追加。
  - 制約: 60Hz、0.1 秒 disconnect sleep、既存 key 名・JSON、共有 2 利用元を維持。

- [ ] **【中】browser 登録後の全経路を finally cleanup で囲む**
  - 現状: `receiver/input_server.py:508-524` は client 登録後、outer `finally` より前に
    `load_config()` と初回 send を行う。破損 JSON/OSError または
    `ConnectionClosed` 以外の初回 send 例外では set から削除されず、失敗接続ごとに
    stale client が 1 件ずつ残り得る。
  - 対応案: 登録後の処理全体を 1 つの `try/finally` にし、初回 send にも timeout を適用。
  - 制約: 接続直後の config 1 件送信と browser message 無視の仕様を維持。

### 2. API・設定・起動フロー

- [ ] **【高】receiver 再起動の GUI/API 契約と安全な終了を揃える**
  - 現状: GUI は `POST /api/restart` (`receiver/config_gui.html:914-920`) だが、backend と
    `docs/api.md` は `DELETE` のみ (`receiver/input_server.py:354-358`) で必ず 404。
    GUI は HTTP status を確認せず 2 秒後に reload する。正式 DELETE 経路にも再入 guard と
    RC 解放がない (`receiver/input_server.py:328-331,466-470`)。
  - 対応案: GUI を DELETE にして `res.ok` を検査し、backend は one-shot guard と
    RC/input cleanup を行ってから exec する。method dispatch と連打の regression test を追加。
  - 制約: 正式 API は DELETE のまま、0.5 秒応答先行、port/path を維持。

- [ ] **【高】Receiver GUI の Sender 設定を 2PC のファイル所有境界に合わせる**
  - 現状: receiver の `/api/sender-config` は Sub PC ローカルの JSON だけを読み書き
    (`receiver/input_server.py:208-213,259-269`) し、Main PC の sender には届かない。
    GUI は host/port の 2 keys だけで全置換 (`receiver/config_gui.html:2061-2070`) し、
    port fallback 3 箇所は旧値 8765 (`receiver/config_gui.html:461,2380,2401`) で、
    現行 receiver WS 既定 8888 と不一致。secretary-bot 本体には同 API の参照 0 件。
  - 対応案: Main PC の live 設定は sender GUI/API (8082) を唯一の運用入口として UI に明記。
    receiver 側 API は互換維持しつつ「receiver-local file」と表示し、保存は既存 JSON へ
    host/port を merge、fallback は 8888、debug は receiver 実 WS port を使う。
  - 制約: `/api/sender-config` と config_change は削除せず、実 config を自動移送しない。

- [ ] **【高】standalone launcher で gamepad 実行依存を導入する**
  - 現状: `start_standalone.bat:19-20` は `websockets pynput` だけを install する一方、
    capture は pygame が無ければ gamepad を無効化する
    (`receiver/standalone_capture.py:85-93`)。standalone は gamepad 対応だが、README は
    現在この 1 dependency だけ手動導入する暫定 workaround を案内している
    (`README.md:40-46,221-230`)。
  - 対応案: standalone launcher の既存 pip install に `pygame` を加え、起動 smoke check を行う。
  - 制約: launcher が依存導入を所有する現行方針、entry point、他 mode の依存を維持。

- [ ] **【高】preset/layout-preset の read-modify-write を 1 transaction にする**
  - 現状: `ThreadingHTTPServer` に対し、lock は各 load/save の中だけ
    (`receiver/input_server.py:50-108`)。POST/DELETE の 4 経路
    (`receiver/input_server.py:224-256,304-325`) は同じ snapshot を並行に読めるため、
    後勝ち save が別 request の追加・削除を失わせる。
  - 対応案: 各 mutation 全体を 1 lock transaction にし、可能なら同一 directory の temp
    file + replace で保存する。2 並行更新の lost-update test を追加する。
  - 制約: JSON shape/path、旧 preset migration、API response、broadcast を維持。

- [ ] **【高】sender の可変 HTTP/monitor port を周辺フローまで一貫させる**
  - 現状: port は config から読まれる (`sender/input_sender.py:578,586`) が、monitor GUI は
    8083 固定 (`sender/sender_gui.html:475-477`)、firewall と自動 open は 8082/8083 固定
    (`start_sender.bat:32-38`)。custom port では input monitor、自動 GUI、LAN firewall の
    3 経路が追従しない。
  - 対応案: GUI は読み込んだ `monitor_port` を使い、launcher は Main PC の config から
    port を安全に取得して firewall/open に渡す。未設定・不正値は既定 8082/8083へ戻す。
  - 制約: 既定 port、既存 config keys、管理者昇格、Gitea pull/install/start 順を維持。

### 3. 保守性・検証

- [ ] **【中】中核常駐フローの regression test を追加する**
  - 現状: coverage.py による 2026-07-11 実測は全 1,784 statements 中 10%。
    `sender/input_sender.py` 368 statements、`sender/monitor_ws.py` 50、
    `input_common/gamepad.py` 162、`receiver/standalone_capture.py` 75 を含む
    9 modules が 0%。既存 unittest は 18 件で、純粋 helper/preset/static path が中心。
  - 対応案: live hook/socket を起動せず、fake WebSocket/pygame/injector と
    `unittest.IsolatedAsyncioTestCase` で queue overflow、reconnect cancellation、
    RC lifecycle、gamepad reset を優先して追加する。coverage は開発時のみ使う。
  - 制約: pytest/新 runtime dependency/CI を追加せず、実 config に触れない。

- [ ] **【低】確定した重複関数と未参照状態だけを削除する**
  - 現状: `receiver/config_gui.html` の `switchOverlayMode` は 2 定義
    (`:1149-1162`, `:2042-2059`) で前者 14 行が後者に上書きされる。
    `receiver/input_server.py` の `_standalone` は宣言・global・代入の 3 箇所
    (`:36,592,595`) に対し読み取り 0 件。
  - 対応案: 前者の重複定義と `_standalone` 3 箇所を挙動不変で削除する。
  - 制約: mode switch、standalone 分岐、single-file HTML 方針を維持。

---

## 対象外と判断したもの（2026-07-11 再確認）

- `receiver/config_gui.html`（2495行）/ `receiver/overlay.html`（1053行）:
  ビルドなし単一ファイル GUI は
  この規模のツールでは意図的な構成のため分割候補にしない。
- 切断まわりの `except: pass`: シャットダウン時のベストエフォート解放で意図的。
- gamepad / raw mouse の 60Hz ポーリング: 変化時のみ送信・累積 flush 済みで
  CPU 負荷は問題なし。

---

## 完了アーカイブ

### 2026-07-11: sender reconnect 時の子 Task を必ず cancel・回収する
検証: `python -m py_compile sender\input_sender.py` OK、
`python -m unittest discover -s tests` OK（54件）、`python -m ruff check .` OK、
`git diff --check` OK。Live 確認（実機での sender 切断・再接続）は未実施
（Main PC sender / Sub PC receiver が必要なため）。実 `config/*.json` の
読み書きなし。

`sender/input_sender.py`: `_send_loop` の各周回を `try/finally` に変更し、
`finally` で `get_task`/`reconnect_task` のうち未完了のものを `cancel()` した
上で必ず `asyncio.gather(get_task, reconnect_task, return_exceptions=True)` を
`await` してから次周回・呼び出し元へ抜けるようにした（reconnect による
`return` 経路・`ws.send` 例外経路のいずれも通る）。`sender()` 側も同様に、
`send_task`/`recv_task` を待つ `try/finally` に変更し、`finally` で未完了の
方を `cancel()` してから両方を `gather(..., return_exceptions=True)` で
回収し、結果に `CancelledError` 以外の例外が含まれていれば `logger.error`
で記録した上で re-raise するようにした（従来は `done` 側の例外を一切
確認しておらず、`_send_loop`/`_recv_from_receiver` が予期しない例外で
終了しても静かに握り潰されていた）。外側の `except (ConnectionRefusedError,
OSError)` / `except websockets.ConnectionClosed` / `except Exception` による
reconnect backoff・remote state 通知はそのまま維持。500 件上限・oldest-drop・
FIFO send の挙動も変更なし。

`tests/test_remote_control.py` に 2 クラス追加（46件→54件）。
`SendLoopReconnectCleanupTests`: 専用の `event_queue`/`_reconnect_event` を
差し替え、reconnect 直後（未送信）サイクルと「1 件 queue → 送信 → reconnect」
サイクルを 3 回繰り返し、各サイクル後に `asyncio.all_tasks()`（自タスク除く）
が空であること（orphan task 0 件）と、3 サイクル終了後に
`fake_ws.sent == ["event-0", "event-1", "event-2"]`（欠落・重複なく厳密に
1 回ずつ送信）であることを検証。`SenderTaskExceptionPropagationTests`:
`_send_loop` が即座に例外を送出し `_recv_from_receiver` が完了しないよう
差し替えた状態で `sender()` を実行し、`logger.error` が該当の例外
インスタンスを `exc_info` 付きで記録すること（＝握り潰されず外側の
reconnect パスまで伝播すること）を確認（`RECONNECT_BACKOFF` はテスト内で
短縮）。

### 2026-07-11: sender 切断時に browser の押下・軸状態を明示的にリセットする
検証: `python -m py_compile receiver\input_server.py` OK、
`python -m unittest discover -s tests` OK（52件）、`python -m ruff check .` OK、
`git diff --check` OK。Live 確認（実機での sender 切断・オーバーレイ表示確認）は
未実施（Main PC sender / Sub PC receiver が必要なため）。実 `config/*.json` の
読み書きなし。

`receiver/input_server.py`: `sender_handler` の `finally` に、
`{"type":"input_reset"}` を `await broadcast_to_browsers(...)` で送る処理を追加。
Remote Control が有効だった場合の `_set_rc_state(False)`（既存の
`remote_control_state` 通知）より前に置き、RC が元々無効だった接続の切断でも
必ず送られるようにした（`was_active` 条件からは独立）。

`receiver/overlay.html`: `ws.onmessage` に `input_reset` 分岐を追加し新設の
`resetDisplayedInput()` を呼ぶ。この関数は `displayDelayTimers` を即座に
`clearTimeout` して空にし、`afterglowTimers` を全 `clearTimeout` した上で
該当要素の `afterglow` クラスを外し、`pressedKeys` に残っていた要素の
`active` クラスを外してから、`pressedKeys`/`dirState`/`axisState` を新設の
共有ヘルパー `clearInputState()`（旧 `mode_switch`/`buildLayout` 内の重複コード
3行を集約したもの）でクリアし、`updateStickVisuals()` でコントローラーの
スティック/トリガー表示を中立へ戻し、`trailPoints = []` でマウストレイルの
蓄積点だけを消す（`trailAnimId`/描画ループは止めない）。`buildLayout()` の
呼び出しや履歴エントリ追加は行わない。`mode_switch` は `clearInputState()` を
呼ぶよう置き換えただけで、挙動は従来通り（続けて `buildLayout()` が呼ばれる）。

`docs/api.md`: `/browser` メッセージ表と §6.3 に `input_reset`
（`sender_handler` cleanup ごとに必ず送信、追加フィールドなし、overlay 側の
リセット内容）を追記。

`tests/test_remote_control.py` に `SenderDisconnectInputResetTests` を追加
（fake sender/browser 接続のみ、実ソケット不使用）。RC が一度も有効化されて
いない切断でも `input_reset` が飛ぶこと、RC 有効化後の切断では
`input_reset` の broadcast が `_set_rc_state(False)` より先に呼ばれることを
`_set_rc_state`/`broadcast_to_browsers` を記録用ラッパーに差し替えて検証。
`tests/test_overlay_input_reset.py` を新規追加（JS 実行なしの静的
regression。`overlay.html` を単一 HTML/JS ファイルとして読み込み、
`input_reset`/`mode_switch` 分岐の呼び出し先、`resetDisplayedInput` が
上記の全カテゴリを clear し `buildLayout()`/`recordCurrentState()` を
呼ばないこと、`clearInputState` が `mode_switch` と共有されていることを
文字列/brace-matching ベースで確認）。既存 message type・browser 接続
ライフサイクル・レイアウト/履歴設定・ポート/依存関係は変更なし。

### 2026-07-11: Remote Control 有効化時に入力抑止を表示待ちより先に確立する
検証: `python -m py_compile sender\input_sender.py` OK、
`python -m unittest discover -s tests` OK（46件）、`python -m ruff check .` OK、
`git diff --check` OK。Live 確認（実機での Remote Control 有効化・オーバーレイ表示）
は未実施（Main PC sender が必要なため）。実 `config/*.json` の読み書きなし。

`sender/input_sender.py`: `_set_remote_mode` の enable 分岐を並べ替え、
`_overlay_manager.show()`（初回 Tk thread ready を最大 3.0 秒同期待ちし得る
`sender/overlay_window.py:69,97-105`）より前に `_ll_mouse_blocker.set_suppress(True)`
と `_restart_listeners(suppress=True)` を完了させるようにした。新しい enable 順序は
`set_user_hidden(False)` → `_freeze_cursor()` → `_ll_mouse_blocker.set_suppress(True)`
→ `_restart_listeners(suppress=True)` → `_overlay_manager.show()`。disable 分岐は
既存の意図（低レベル抑止解除 → overlay 非表示 → cursor unfreeze → listener を
suppress なしへ復帰）をそのまま維持しつつ、`_restart_listeners` 呼び出しを
分岐内の末尾へ移し順序を明示化した（if/else 後の共通呼び出しを廃止）。
cursor freeze・Pause による一時非表示・user-hidden 状態・二重防護設計
（`_ll_mouse_blocker` + pynput suppress listener）は変更なし。overlay thread の
prewarm は導入せず（handoff の "Prefer ordering only" 方針どおり、順序変更のみで
表示待ち中の入力抑止という目的を満たすため）。

`tests/test_remote_control.py` に `RemoteModeSuppressionOrderingTests` を追加
（既存 42 件 + 新規 4 件 = 46 件）。`FakeOverlayManager` / `FakeLLMouseBlocker` と
`_restart_listeners`/`_freeze_cursor`/`_unfreeze_cursor` の関数差し替えで
`_set_remote_mode` の呼び出し順序だけを記録し、enable 時に
`ll_suppress(True)` と `restart_listeners(True)` が `overlay.show()` より前に
完了すること、disable 時が `ll_suppress(False)` → `overlay.hide()` →
`unfreeze_cursor()` → `restart_listeners(False)` の順であること、同一モードへの
repeat 呼び出しが完全な no-op（呼び出し 0 件）であることを検証。実フック・Tk・
ソケット・OS 入力は一切使用しない。

### 2026-07-11: Remote Control の接続遷移を fail-closed にする
検証: `python -m py_compile sender\input_sender.py receiver\input_server.py` OK、
`python -m unittest discover -s tests` OK（42件）、`python -m ruff check .` OK、
`git diff --check` OK。Live 確認（実機での sender 接続・切断・API 呼び出し）は
未実施（Main PC sender / Sub PC receiver が必要なため）。実 `config/*.json` の
読み書きなし（このマシンに `config/sender_config.json` 自体が存在せず、
`load_config()` はデフォルト値のコピーを返すのみ）。

`receiver/input_server.py`: 新設の `_sender_synchronized`（bool、`_rc_lock` で
保護）を追加。sender 接続確立時に `sender_ws` と併せて `False` にリセットし
（stale な `remote_control_enabled` が残っていても fail-closed）、
sender からの `remote_control` メッセージ（唯一の同期確立根拠）受信時にのみ
`_set_rc_state(enabled, mark_synchronized=True)` で `True` にする。切断時は
`finally` 内で `sender_ws`/`_sender_synchronized` を同一ロック内でリセットし、
RC が有効だった場合は追加で無効化する。`_rc_inject_event` は
`remote_control_enabled` に加えて `_sender_synchronized` も必須条件にした。

`/api/remote-control` の POST を有効化/無効化で分岐: 無効化 (`enabled: false`)
は sender の有無に関わらず即座に `_set_rc_state(False)` してから
`_notify_sender_async`（結果を待たない best-effort 通知）を送る。有効化
(`enabled: true`) は `_sender_ready()`（接続済みかつ同期済み）でなければ
新設の `ApiError`（HTTP status 付き例外）で 409 を返し、状態変更・コマンド送信
を一切行わない。準備済みなら `_send_command_to_sender`（HTTP ハンドラスレッドから
`asyncio.run_coroutine_threadsafe` + 1 秒の bounded `.result()` 待ちで、
制御プレーンのみ・入力経路は塞がない）で送信するが、成功してもローカルの
`remote_control_enabled` はまだ変更しない。実際の有効化は sender からの
状態報告（sender_handler 経由の `_set_rc_state(..., mark_synchronized=True)`）
を受けて初めて行われ、送信自体が失敗すれば 502 を返し状態も変えない。
`_dispatch` は `ApiError` を専用に捕捉し `{"error": ...}` を該当 status で返す
（`{ok: false}` + 200 にはしない）。

`sender/input_sender.py`: 接続確立直後の状態通知を `if remote.mode:` の
条件分岐から常時送信に変更（`await ws.send(...)` は enabled の True/False
どちらでも必ず、通常入力の送信/受信ループ開始より前に実行）。

`receiver/config_gui.html`: `toggleRemoteControl` が `res.ok` を確認し、
非 2xx 時は `data.error` をステータス表示するだけでボタン状態は変えないよう
修正（成功時は従来通り要求値を暫定表示し、実際の確定は
`remote_control_state` ブロードキャストに委ねる）。

`docs/api.md`: `POST /api/remote-control` の有効化/無効化の非対称な挙動
（fail-closed の 409/502、pending-enable、best-effort disable notify）と、
sender→receiver `remote_control` メッセージが「同期確立の唯一の根拠」であり
接続直後に ON/OFF いずれでも必ず送られる点を追記。

`tests/test_remote_control.py` に追加（既存 17 件 + 新規 25 件 = 42 件）:
`SenderHandlerReadinessTests`（fake の sender 接続で
`sender_handler` を駆動: 状態メッセージ受信前は stale な enabled=True でも
注入されないこと、初回 false 受信で OFF 維持、初回 true 受信後にのみ注入、
切断でのリセットと再接続後の再同期要求）、`RemoteControlApiGatingTests`
（`_send_command_to_sender`/`_notify_sender_async` を fake に差し替え、
sender 不在/未同期時の 409・状態不変・コマンド未送信、同期済み時の送信は
するがローカル enable は ack までしないこと、送信失敗時の 502、無効化が
sender 不在でも即座に効くこと）、`SenderAnnouncesExplicitStateOnConnectTests`
（`websockets.connect` と `_send_loop`/`_recv_from_receiver` を fake/no-op に
差し替えて `sender()` を駆動し、接続直後の最初の送信が enabled True/False
いずれの場合も明示的な `remote_control` メッセージであることを確認）。
いずれも実ソケット・実 asyncio イベントループの共有状態に依存しない
fake/seam のみを使用。

### 2026-07-11: 押下中入力を実 VK 単位かつ原子的に追跡して stuck key を防ぐ
検証: `python -m py_compile receiver\input_server.py receiver\input_injector.py` OK、
`python -m unittest discover -s tests` OK（31件）、`python -m ruff check .` OK、
`git diff --check` OK。Live 確認（実機での Remote Control 実注入）は未実施
（Main PC sender / Sub PC receiver が必要なため）。実 `config/*.json` の読み書きなし。

`receiver/input_injector.py`: `_send_input` が `SendInput` の戻り値（挿入成功
件数）を見て bool を返すようにし、`inject_key` / `inject_mouse_button` もその
成否を返す。`replay_event` は成功した注入についてのみ、キーボードは
`("vk", <int>)`、マウスボタンは `("mouse", "<mouse_*>")` というタグ付き
tuple の識別子を返すよう変更（mouse_move/scroll・失敗・未対応イベントは
`None`）。名前表からの VK 再構成に依存していた `release_all` は削除し、
代わりに識別子をそのまま解放する `release_identities` を追加（右 Shift/Ctrl/Alt
の VK `161/163/165` や Win キー `91/92` も表示名を経由せず正確な VK で解放）。

`receiver/input_server.py`: `_rc_pressed_keys`（表示名 set）を
`_rc_active_identities`（識別子 set）に置き換え。新設の `_rc_inject_event`
が「有効状態チェック → 注入 → 追跡更新」を単一の `_rc_lock` 臨界区間内で
アトミックに行うようにし、`sender_handler` はこれを呼ぶだけに簡素化。
`_set_rc_state` は無効化時、`_rc_lock` 内で追跡集合を snapshot して clear し、
ロック外でその snapshot だけを `release_identities` で解放する。これにより
OFF と競合する進行中の key-down は「_rc_inject_event 全体が disable の
snapshot より前に完了して解放対象に含まれる」か「有効チェックの時点で
即座に拒否される」のいずれかにしかならず、解放後に注入・追跡が残る経路が
なくなる。

`tests/test_remote_control.py` を新規追加（17件）。`input_injector` 単体は
実モジュールを import し `_send_input` のみ fake に差し替えてテスト
（右 Shift/Ctrl/Alt・Win キーの正確な VK 解放、左右同時押しの独立性、
マウスボタンの正確な解放経路、注入失敗時・未対応ボタン時に識別子を
追跡しないこと）。`input_server` 側は fake injector で `_rc_inject_event` /
`_set_rc_state` のロック・ライフサイクルを検証（有効時のみ注入、繰り返し
up/down の無害性、disable 時の正確な snapshot 解放、そして key-down と
disable の競合を `_rc_lock.locked()` を使った同期フックで決定的に再現し
リーク・OFF 後注入が起きないことを確認）。sleep をアサーションには使用せず。

イベント JSON・API ルート・注入順序（成功時のみ追跡更新）・切断時
auto-disable・browser への状態ブロードキャストは変更なし。`docs/api.md` の
「OFF 時の解放」記述を `release_all` から `release_identities` ベースの
説明に更新。

### 2026-07-09: lint とテストの最低限を整備する
検証: `python -m unittest discover -s tests` OK（18件）、`python -m ruff check .` OK
（ローカル環境に ruff 0.15.11 導入済みのため実行）、`python -m py_compile
sender\input_sender.py sender\gamepad.py receiver\standalone_capture.py
input_common\input_events.py input_common\gamepad.py receiver\input_server.py`
OK、`git diff --check` OK。
新規 `pyproject.toml` に `[tool.ruff]`（`target-version = "py311"`）を追加。
デフォルトのルールセットのまま、`input_common` を import するための
`sys.path` ブートストラップ（前回 handoff で導入済み・意図的）を持つ
`receiver/standalone_capture.py` / `sender/input_sender.py` /
`sender/gamepad.py` と、同じパターンを使う `tests/*.py` にのみ
`E402` の per-file-ignore を追加。それ以外は既定ルールのまま全ファイル
`ruff check .` が通る状態。実行時に検出された未使用 import
（`sender/overlay_window.py` の `from ctypes import wintypes`）は
1行の obviously-correct な削除として対応（動作変更なし）。
新規 `tests/`（`unittest` のみ、pytest 不使用）に3ファイル・18ケースを追加:
`test_input_events.py`（`make_event` の JSON 形状、`key_to_str` の
VK A-Z/0-9 正規化・`char`/`name`/`vk_<code>` フォールバック、`get_vk` の
直接 `vk`/`value.vk` フォールバック/該当なし）、`test_receiver_presets.py`
（`load_presets`/`save_presets`/`load_layout_presets`/`save_layout_presets`
を `tempfile.TemporaryDirectory()` 上へ `PRESETS_PATH`/`LAYOUT_PRESETS_PATH`
を一時的に差し替えてテスト。実 `config/*.json` は未使用・未変更）、
`test_receiver_static_path.py`（`OverlayHandler._resolve_static_path` を
`object.__new__` で未初期化インスタンス化して直接呼び出し。正常系
`overlay.html` の解決と、`../config/config.json` / `C:/Windows/win.ini`
の拒否を確認）。いずれもキーボード/マウス/ゲームパッドの実フックや
サーバー起動を行わない（`input_server` モジュールはサーバー起動処理が
`if __name__ == "__main__"` 配下にあるため import のみで副作用なし）。
ルート/ポート/API/JSON メッセージ形状・`.bat` ランチャーは変更なし。

### 2026-07-09: standalone_capture のキー正規化・gamepad polling の重複を解消する
検証: `python -m py_compile sender/input_sender.py sender/gamepad.py
receiver/standalone_capture.py input_common/input_events.py
input_common/gamepad.py` OK、`git diff --check` OK。加えて `sender/` `receiver/`
それぞれをカレントディレクトリ扱いで `sys.path[0]` に見立てた状態からの
import 疎通確認済み（`python sender/input_sender.py` /
`python receiver/input_server.py --standalone` 相当）。Live 確認
（実機での sender キャプチャ・standalone gamepad 実接続）は未実施。
新規 `input_common/`（`input_events.py`: `key_to_str` / `get_vk` /
`make_event`、`gamepad.py`: `Gamepad` クラス本体）に共有実装を切り出し。
`sender/gamepad.py` は `input_common/gamepad.py` を re-export する薄い
互換ラッパーに変更（`import gamepad as gamepad_mod` は変更なしで動作）。
`sender/input_sender.py` は `_MODIFIER_MAP` / `key_to_str` / `make_event` /
`_get_vk` のローカル実装を削除し `input_common.input_events` から import。
`receiver/standalone_capture.py` はキー正規化ヘルパーと重複 gamepad polling
ループを削除し、`input_common.gamepad.Gamepad(emit_callback=_emit,
is_running=lambda: _running)` を使う薄いラッパーに置換（pygame 未導入時は
従来通り `"[Standalone] pygame not found - gamepad disabled"` を出して
スレッドを終了）。両ファイルとも `Path(__file__).resolve().parent.parent`
をリポジトリ直下として `sys.path` に追加するブートストラップを追加し、
サブディレクトリからの直接スクリプト実行でも `input_common` を import
できるようにした。イベント JSON 形状・キー名（`btn_<i>` /
`hat_<i>_left|right|up|down` / `axis_<i>_neg|pos` / `axis_update`）・
60Hz ポーリング・切断時スリープ・ルート/ポート/API は変更なし。

### 2026-07-09: `sender/input_sender.py` の責務を分割する
検証: `python -m py_compile sender\input_sender.py sender\http_api.py sender\monitor_ws.py` OK、
`git diff --check` OK。Live 確認（Main PC での sender GUI / monitor WS 実接続）は未実施。
`SenderHTTPHandler` / `start_http_server` を新規 `sender/http_api.py` へ、monitor WS
（`monitor_handler` / `monitor_broadcaster` / `start_monitor_ws`）を新規
`sender/monitor_ws.py`（`MonitorServer` クラス）へ抽出。`input_sender.py` から
`http_api.py` へは `SenderContext`（config アクセサ・gamepad アクセサ・
`trigger_reconnect`・ステータス取得コールバック群）を介して渡し、グローバル変数の
逆 import は行わない。`enqueue_monitor` は `input_sender.py` 側に薄いラッパーとして
残し、実体は `MonitorServer.enqueue`。ルート・ポート・JSON 形状・config 変更時の
再接続条件（host/port 変化時のみ）・restart の応答順序・monitor の
thread-safe enqueue とクライアント切断時の discard 挙動は変更なし。
`docs/api.md` は実装ファイルの参照先のみ更新（`SenderHTTPHandler` →
`sender/http_api.py`、`monitor_handler` → `MonitorServer` in `sender/monitor_ws.py`）。

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
