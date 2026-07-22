# プログラム改善チェックリスト

コードベースを調査して洗い出した改善候補の一覧
（初回調査: 2026-07-08 / 再調査: 2026-07-08、2026-07-11）。

**運用方法**: 着手したい項目にチェック `[x]` を入れる → Codex が handoff を作成し、
Claude Code（`claude -p --model sonnet --effort medium --permission-mode auto "<handoff/task prompt>"` / Sonnet）が
実装する。handoff を挟むまでもない
小粒な項目は Claude Code に直接依頼してもよい。
実装完了した項目は「完了アーカイブ」へ移動する。

- 機能追加・未検証項目はこのファイルの対象外。
- 優先度: **高** = 常駐運用での安定性に直結 / **中** = 保守性・性能 / **低** = 任意。
- sender は Main PC 常駐・receiver は Sub PC 常駐のため、無制限に増える
  メモリ/キューと入力イベント経路の遅延が最優先の観点。

---

## 改善候補

現時点で残っている改善候補はありません（2026-07-11 時点、13件すべて実装・
検証済み。詳細は「完了アーカイブ」の 2026-07-11 セクションを参照）。

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

### 2026-07-11: 改善候補13件（phase 1/2 handoff）の実装完了

phase 1（resident-stability、handoff
`docs/handoffs/archive/2026-07-11-all-improvements-phase-1-resident-stability.md`
+ review fix `docs/handoffs/archive/2026-07-11-phase-1-review-fixes.md`）と
phase 2（API・GUI・launcher、handoff
`docs/handoffs/archive/2026-07-11-all-improvements-phase-2-api-launcher-gui.md`）
で、当時の改善候補13件すべてを実装。Codex レビューで atomic legacy-preset
migration と receiver debug-port 上限チェックの2件の小さな整合性修正を追加し、
以下の最終検証を実施済み：
`python -m py_compile`（対象 Python module 全件）: OK、
`python -m unittest discover -s tests`: OK（130件）、
`python -m ruff check .`: OK、`git diff --check`: OK（改行コード警告のみ、
エラーなし）。実機が必要な Live 確認（Main PC sender / Sub PC receiver 実入力、
物理 gamepad / Raw Input、実 WebSocket slow client、launcher の
package install/firewall/browser open、実際の receiver restart）は今回も
未実施としてブロック記録。実 `config/*.json` の読み書きは一切なし
（例示 config・temp/fake/static test のみ使用）。

#### 1. sender monitor queue/send の有界化

`sender/monitor_ws.py` の `MonitorServer` に `asyncio.Queue(maxsize=500)` を
導入。`enqueue()` は `call_soon_threadsafe` でキャプチャスレッドから安全に
loop-thread の `_enqueue_on_loop` を呼び、client 0 件時は即 drop、満杯時は
oldest 1 件を `get_nowait` してから最新を積む。`_broadcaster` は
`asyncio.gather` でクライアントへの送信を同時実行し、各送信は
`_send_to_client` で 1.0 秒 timeout（`_CLIENT_SEND_TIMEOUT`）。失敗クライアントは
discard 後に `_close_client` で同じ 1.0 秒 timeout の bounded close を
これも `asyncio.gather` で同時実行し、close が stall/例外を起こしても
broadcaster や healthy client への配信を止めない（Codex レビューで追加された
close-side の有界化）。`tests/test_monitor_ws.py`（5件）で slow/healthy
client の独立配信、overflow の oldest-drop、client 0 件時の drop、
close-timeout でも broadcaster が継続することを検証。port 8083・JSON 形状・
receiver 切断中の local monitor 独立動作は変更なし。

#### 2. receiver browser fan-out と RC 注入の独立化

`receiver/input_server.py` の `sender_handler` で `_rc_inject_event(event)` を
`broadcast_to_browsers` の await より前に実行するよう順序変更。
`broadcast_to_browsers` は client ごとに `asyncio.gather` で同時送信し、
1.0 秒 timeout・失敗 client discard は維持したまま、1 client の stall が
他 client や RC 注入を遅らせない構造にした。`tests/test_remote_control.py`
の `BroadcastToBrowsersConcurrencyTests`・
`SenderHandlerInjectsBeforeBroadcastCompletesTests` で、stalled 1件 +
healthy 1件構成でも healthy 配信が stall を待たずに完了すること、RC 注入が
stalled browser send の完了を待たないことを検証。message 順・payload・RC
条件は変更なし。

#### 3. standalone 入力 queue の有界化とオーバーフロー時の入力リセット方針

`receiver/input_server.py` の standalone queue を `asyncio.Queue(maxsize=500)`
にし、producer 側の enqueue はすべて asyncio loop thread 上で行う（capture
コールバックは thread-safe なまま）。通常時は FIFO、満杯時は既存の
backlog を全クリアしてから `{"type": "input_reset"}` を積み、続けて最新
event を積む方針（key-down/up の欠落より overlay の stuck 表示を避ける
ことを優先）。overflow 警告ログは5秒に1回まで rate limit。
`tests/test_standalone_queue.py`（4件）で通常時 FIFO、overflow 時の
backlog クリア + `input_reset` + 最新 event の順序、ログの rate limit、
queue 未設定時の no-op を検証。standalone の JSON 形状・60Hz throttling・
keyboard/mouse/gamepad 対応は変更なし。

#### 4. Raw Input の fallback flush と失敗経路の resource cleanup

`sender/raw_mouse.py`: `SetTimer` の成否を保持し、失敗時は 16ms の
`MsgWaitForMultipleObjectsEx` wake ごとにメッセージ dispatch 後、蓄積 delta を
flush するようにした（従来は WM_TIMER 前提で fallback 表示のみ・実送信
0件/s だった経路を修正）。`timeBeginPeriod(1)` 実行直後から、
`GetModuleHandleW(None)` を含む以後の全 resource acquisition
（レビューで `GetModuleHandleW` も対象に追加）を単一 `try/finally` に収め、
リソースの flag/参照は取得前にまず初期化してから acquire するようにした
ことで、途中の早期 return 経路でも `timeEndPeriod`・installed timer の
kill・created window の destroy・登録済み window class の unregister が
必ず実行される（cleanup 自体の失敗は best-effort/log）。
`tests/test_raw_mouse.py`（9件）で fake ctypes/Win32 objects により、
timer 失敗時の 16ms ごとの flush、`GetModuleHandleW` が例外を送出しても
`timeEndPeriod(1)` が実行され不正な cleanup が走らないことを含めて検証。
16ms throttle・delta 精度・background capture・message shape は変更なし。

#### 5. 共有 Gamepad の neutralize とリトライ

`input_common/gamepad.py`: 物理切断・controller 切替・session 例外・
shutdown いずれの state clear 前にも、active な button/hat/threshold axis の
`key_up` と非中立 raw axis の `axis_update` 値 `0` を emit してから3つの
state buffer をクリアし joystick 参照を解放するようにした（controller 切替は
新 joystick 割り当て前に旧 state を neutralize する順序）。`self._pygame` は
`pg.init()`/`pg.joystick.init()` 呼び出し前に代入するよう変更し、init 途中
失敗でも teardown 可能にした（レビュー指摘）。outer session の `finally` は
neutralize → 3 buffer clear → `state["joy"]=None` の順で行った上で、
neutralize が例外を送出しても pygame teardown は必ず実行し、joystick quit と
global quit も互いに独立した best-effort 呼び出しにして一方の失敗が他方を
スキップしないようにし、`_pygame` は常に `None` に戻す。`run()` は
init/scan/pump/getter の例外を捕捉し、0.1 秒始動・2.0 秒上限の exponential
backoff で再試行し、polling に到達したら backoff をリセットする。
`tests/test_gamepad.py`（8件）で fake pygame/joystick により、切断・切替時の
neutralize、partial-init 失敗時の teardown 可否、neutralize/joystick-quit
失敗時でも teardown が継続すること、一過性例外からの回復を検証。60Hz
polling・0.1秒 no-controller sleep・既存 key 名/JSON・sender/standalone
共有は変更なし。

#### 6. browser 登録後の finally cleanup 一本化

`receiver/input_server.py`: browser を `browser_clients` に登録した後の
`load_config()` と初回 config send を含む処理全体を単一 `try/finally` に
収め、初回 send にも既存の 1.0 秒 timeout を適用した。破損 JSON/OSError や
`ConnectionClosed` 以外の初回 send 例外でも必ず set から discard される。
`tests/test_remote_control.py` の `BrowserHandlerCleanupTests` で
non-`ConnectionClosed` の load/send 失敗時にも client が確実に discard
されることを検証。接続直後 config 1件送信・以後の browser message 無視の
仕様は変更なし。

#### 7. receiver DELETE restart の契約・one-shot・安全な cleanup

`receiver/config_gui.html` の `restartServer()` を `DELETE` に変更し
`res.ok` を確認、失敗時はエラー表示のみで reload しない（成功時のみ既存の
2秒後 reload）。`receiver/input_server.py` に module-level
`threading.Lock` + pending フラグを追加し、最初の `DELETE` のみ restart
thread を起動、pending 中の連打は同じ `{"ok": true}` を返すだけで thread を
増やさない。`_restart_server` は既存の 0.5 秒 sleep（応答を先行させる）の後、
`os.execv` 前に `_set_rc_state(False)` で追跡中の注入をすべて解放し、
standalone 有効時（`_standalone_queue is not None` で判定）は
`standalone_capture.stop()` を best-effort 実行してから exec する。
cleanup/exec 失敗時は log の上で lock 内 pending guard をリセットし再試行
可能にする。`tests/test_receiver_restart.py`（8件）で POST 不在・DELETE
dispatch・連打時に thread が1つだけ起動すること・RC/standalone cleanup が
exec より先行すること・0.5秒 delay 前に応答が返ること・exec 失敗時に guard が
解除されることを fake のみで検証（実プロセス再起動は行わない）。正式 API は
`DELETE /api/restart` のまま、port/path は変更なし。

#### 8. receiver-local sender config の所有境界・UI・merge・8888・debug port

`receiver/config_gui.html`: セクションを Sub PC ローカルコピーである旨・
常駐 Main PC sender は構成しない旨・実運用の変更は Main PC sender
GUI/API（既定 8082）で行う旨を明記するラベルに変更。旧 `8765` 固定の
fallback 3箇所をすべて `8888` に統一。`receiver/input_server.py` の
`POST /api/sender-config` は `_config_io_lock` 配下の単一 read-modify-write
transaction にし、既存 receiver-local JSON（無ければ `{}`）を読み込んで
`host`/`port` のみ更新・他 key は保持したまま保存し、merge 後の完全な
object を broadcast する（他の incoming key は無視）。debug WebSocket は
receiver-local sender config ではなく、receiver プロセス実際の `_ws_port` を
使うようにし、`_inject_ws_port()` が `config_gui.html` 配信時に `<head>` へ
`window.__WS_PORT__=<int>` を注入するようにした。`connectDebug()` は
その注入値を使い、`Number.isInteger(...) && >=1 && <=65535` の範囲チェック
（Codex レビューで追加された上限チェック）を通らない場合のみ `8888` に
fallback する。`tests/test_receiver_config_transactions.py` の
`SenderConfigMergeTests`（4件）で merge・他 key 保持・extra key 無視・
broadcast の merged full object を、`InjectWsPortTests`（3件）で実際の
整数 port 注入・`<head>` 一箇所のみへの注入を検証。`/api/sender-config` と
`config_change` は削除・仕様変更なし、実 config の自動移送も行わない。

#### 9. standalone launcher の pygame 依存導入

`start_standalone.bat` の既存 `pip install` 行に `pygame` を追加（fetch/pull
/install/start の順序・entry point は変更なし）。`README.md` の standalone
説明から pygame 手動導入の暫定 workaround・関連する制限記述を削除し、
launcher が導入する旨に更新。`tests/test_launcher_batch_static.py` に
`start_standalone.bat` のコミット済みテキストのみを対象とした静的
regression（サーバー起動・package install なし）を追加。

#### 10. preset/layout-preset の transaction 化とアトミック保存

`receiver/input_server.py`: `_config_io_lock` を `threading.RLock` に変更し、
mutation 全体を保持したまま内側で public load/save ヘルパーを呼べるように
した。新設の `_atomic_write_json()`（対象ファイルと同一ディレクトリの
temp file に書き込み後 `os.replace`、失敗時は temp file を best-effort
削除）を `save_presets`/`save_layout_presets`、および上記8の
receiver-local sender-config merge の保存にも使用。preset/layout-preset の
POST/DELETE それぞれの read-modify-write 全体を1つの outer
`_config_io_lock` transaction にし、broadcast/log は transaction 成功・
lock 解放後にのみ行う。旧 preset のフラット形式 migration
（`load_presets` 内、`_PRESET_TYPES` に一致しないキーがあれば
`{"keyboard":{},"leverless":{},"controller":{}}` へ変換）の書き戻しも
`_atomic_write_json` 経由に変更（Codex レビューでの整合性修正）。
`tests/test_receiver_config_transactions.py` の `AtomicWriteJsonTests`
（3件）で round-trip・temp file が対象ディレクトリに作られること・
replace 失敗時に temp file が残らないことを、`PresetTransactionTests`/
`LayoutPresetTransactionTests`（各2件、二重 thread + 最初の書き込みが
transaction 保持中に待機する決定的な同期フックで検証、sleep による
アサーションなし）で並行 add/delete・update が互いに失われないことを検証。
JSON shape/path・legacy 移行結果・API response・broadcast 内容は変更なし。

#### 11. sender 可変 HTTP/monitor port のエンドツーエンド一貫性

`sender/input_sender.py` の config デフォルトに `http_port: 8082`・
`monitor_port: 8083` を追加し、1..65535 の整数/10進文字列のみ受け付け
bool・範囲外・不正値は既定値へフォールバックする純粋な port 正規化
ヘルパーを追加、HTTP/monitor サーバー起動前に適用（`tests/test_sender_ports.py`
9件で単体検証）。`sender/sender_gui.html` は読み込んだ config を保持し、
`monitor_port` を同じ規則で正規化した上で input monitor 接続先に使用、
初期化は config 読み込み完了を待ってから最初の monitor 接続を行う
（既存の2秒再接続は選択済み port を再利用）。`start_sender.bat` は
git pull 後・firewall/browser 起動前に既定 8082/8083 を設定し、Main PC 実
`config/sender_config.json` が存在すれば安全に読み取って各 port を
1..65535 で検証、妥当な数値のみ batch 変数へ反映（壊れている/存在しない
場合は既定のまま、config を eval/実行しない）。firewall の `localport` と
自動で開く `http://localhost:<http_port>/` の両方に反映後の変数を使用。
`tests/test_sender_gui_static.py`（4件）で GUI の読み込み順・port 使用を、
`tests/test_launcher_batch_static.py` の残り6件で launcher の既定値/
config 由来変数の静的使用（netsh/start 実行なし）を検証。既定 port・
config keys・管理者昇格・pull/install/start 順は変更なし。
`POST /api/config` は引き続き `http_port`/`monitor_port` を無視し
（手動ファイル編集 + 再起動が port 変更手段）、README/docs/api.md の
「8082/8083 固定」記述を更新。

#### 12. 中核常駐フローの regression test

上記1〜11のテストに加え、`tests/test_monitor_ws.py`・`tests/test_raw_mouse.py`・
`tests/test_gamepad.py`・`tests/test_standalone_queue.py`・
`tests/test_receiver_restart.py`・`tests/test_receiver_config_transactions.py`・
`tests/test_sender_ports.py`・`tests/test_sender_gui_static.py`・
`tests/test_launcher_batch_static.py` を新規追加（いずれも `unittest`
標準ライブラリのみ、fake WebSocket/pygame/injector/HTTP と temp dir を使用、
live hook・実 socket・実 config には触れない）。`tests/test_remote_control.py`
にも本アーカイブの1・2・6項のクラスを追加。coverage.py は開発時のみの
参考指標として使用し、新規 runtime dependency・pytest・CI は追加していない。

#### 13. 重複 `switchOverlayMode` と未参照 `_standalone` の削除

`receiver/config_gui.html` の先に定義されていた `switchOverlayMode`
（後の定義に上書きされていた重複、旧 `:1149-1162` 相当）を削除し、後方の
実効定義のみを残した。`receiver/input_server.py` の `_standalone`
（宣言・`global` エントリ・代入の3箇所、読み取り参照0件だった未使用
状態）を削除し、restart cleanup 判定は既存の `_standalone_queue is not None`
を使用。mode switch・standalone 分岐・single-file HTML 方針の挙動は
変更なし。

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
