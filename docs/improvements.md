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

### 1. Remote Control の安全性

- [ ] **【高】押下中入力を実 VK 単位かつ原子的に追跡して stuck key を防ぐ**
  - 現状: `receiver/input_server.py:42,175-187,547-556` は表示用の
    `key` 文字列だけを set に保存し、状態判定・注入・追跡・OFF 時の解放を
    同じロックで直列化していない。注入はイベントの実 VK を使う一方
    (`receiver/input_injector.py:203-215`)、一括解放は名前表から VK を再構成する
    (`receiver/input_injector.py:67,221-235`)。右 Shift/Ctrl/Alt の VK
    `161/163/165` は左側 `160/162/164` として解放され、Win キー VK `91/92`
    は解放対象が 0 件になる。OFF と進行中の key-down が競合すると、解放後に
    down/add が走ってキーが残る経路もある。
  - 対応案: 注入成功時の keyboard VK / mouse button を実入力単位で保持し、
    state 判定から追跡更新までを同じ同期境界で処理する。disable はロック内で
    snapshot+clear、ロック外で正確な release を行い、左右同時押しと OFF 競合の
    unittest を追加する。
  - 制約: イベント JSON、通常の注入順、切断時 auto-disable、入力経路の低遅延を維持。

- [ ] **【高】Remote Control の接続遷移を fail-closed にする**
  - 現状: sender 不在でも `POST /api/remote-control` は先に ON にして成功を返す
    (`receiver/input_server.py:166-172,278-287`)。後から接続した sender は自身が
    OFF なら状態通知を送らない (`sender/input_sender.py:428-430`) ため、Main PC
    が通常モードのまま入力を Sub PC に注入し得る。
  - 対応案: sender 不在時の enable を拒否し、新規接続時は明示的な OFF 同期が済むまで
    注入しない。sender の接続・切断・再接続を通した状態遷移テストを追加する。
  - 制約: 正常時の GUI/API 操作と切断時 auto-disable を維持。

- [ ] **【高】Remote Control 有効化時に入力抑止を表示待ちより先に確立する**
  - 現状: `sender/input_sender.py:147-166` は `remote.mode=True` の後、overlay の
    `show()` を低レベル mouse blocker と suppress listener より先に呼ぶ。初回
    `show()` は Tk thread の ready を最大 3.0 秒同期待ちする
    (`sender/overlay_window.py:69,97-105`) ため、その間 Main PC への入力抑止が
    未確立で、async receiver handler からの切替では event loop も止まる。
  - 対応案: low-level blocker と suppress listener を先に有効化し、overlay thread は
    起動時 prewarm または non-blocking command 化する。初回 enable の順序を fake
    manager/listener で検証する。
  - 制約: overlay 表示、Pause による一時非表示、cursor freeze、二重防護を維持。

- [ ] **【高】sender 切断時に browser の押下・軸状態を明示的にリセットする**
  - 現状: sender cleanup (`receiver/input_server.py:559-567`) は RC 状態だけを解除し、
    browser へ入力 reset を送らない。browser WebSocket は接続したままなので、切断時に
    key-down 中だった keyboard/mouse/gamepad と最後の axis 値は matching up が来ず、
    `receiver/overlay.html:816-870,1014-1046` に残り続ける。
  - 対応案: 後方互換な `input_reset` message を broadcast し、delay timer、pressed、
    direction、axis、afterglow を全て clear する。`docs/api.md` と frontend の状態テストを同期。
  - 制約: 既存 message type、browser 接続、レイアウト・履歴設定を壊さない。

### 2. 常駐キュー・入力ソース・WebSocket

- [ ] **【高】sender reconnect 時の子 Task を必ず cancel・回収する**
  - 現状: `_send_loop` は各周回で `Queue.get()` と `Event.wait()` の 2 Task を作る
    (`sender/input_sender.py:397-409`)。receiver 側の受信終了時、外側は send task を
    cancel するだけで await しない (`sender/input_sender.py:433-445`)。idle 切断 1 回で
    orphan `Queue.get` 1 件 + `Event.wait` 1 件が残り得て、切断 N 回なら最大 N 件の
    先頭入力欠落と 2N Task 残留になる。
  - 対応案: `_send_loop` の `finally` で両子 Task を cancel して
    `gather(return_exceptions=True)` し、外側も send/recv task を cancel 後に await する。
    repeated-cancel 後に orphan 0 件・次イベント送信 1 件を検証する。
  - 制約: 500 件上限、oldest-drop、FIFO、reconnect backoff、remote state 通知を維持。

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

### 3. API・設定・起動フロー

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

### 4. 保守性・検証

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
