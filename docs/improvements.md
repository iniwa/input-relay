# プログラム改善チェックリスト

コードベースを調査して洗い出した改善候補の一覧
（初回調査: 2026-07-08 / 再調査: 2026-07-08 二巡目）。

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

（2026-07-09 時点で残項目なし）

---

## 対象外と判断したもの（2026-07-08 調査メモ）

- `receiver/config_gui.html`（2495行）: ビルドなし単一ファイル GUI は
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
