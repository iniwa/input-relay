# input-relay JSON API リファレンス

> 調査日: 2026-04-15（最終更新: 2026-09-05）
> 対象: `receiver/input_server.py`, `sender/input_sender.py`, `sender/http_api.py`,
> `sender/monitor_ws.py`, `input_common/gamepad.py`

外部管理ツール (secretary-bot 等) から LAN 経由で input-relay の設定 CRUD と状態取得を行うための仕様。実装と乖離しないよう、実ソースから確認した挙動のみを記載する。

---

## 1. 概要

input-relay は以下の 2 プロセスで構成される (単独モードでは receiver のみ)。

| プロセス | 役割 | HTTP | WebSocket |
|---------|------|------|-----------|
| receiver | OBS オーバーレイ表示, 設定 GUI, sender 入力受信, リモート操作のホスト | 8081 (既定) | 8888 (既定) |
| sender | キーボード/マウス/ゲームパッド入力をキャプチャして receiver に送信。2PC 間のテキスト clipboard client | 8082 (既定) | 8083 (監視用, 既定) |

- すべて `0.0.0.0` で listen し、LAN 公開前提。
- **認証はない。** LAN 内信頼ゾーンでのみ使用すること。
- HTTP は `ThreadingHTTPServer` で動くため複数クライアントから同時アクセス可能。
  preset/layout-preset と receiver-local sender-config の POST/DELETE は
  read-modify-write 全体を 1 つの `_config_io_lock` (RLock) transaction として
  実行し、保存は同一ディレクトリの temp file + `os.replace` による atomic write。
- receiver のポートはコマンドライン引数 (`--port` `--http-port`) で変更可能。
- sender のポートは `sender_config.json` の `http_port` / `monitor_port` キーで変更可能
  (キーが無い/不正な値の場合は既定の 8082 / 8083 に正規化される)。`start_sender.bat`
  の firewall ルールとブラウザ自動起動、Sender GUI の入力モニタ接続先はいずれも
  この値に追従する。通常運用では変更不要なため既定の 8082 / 8083 のままでよい。
- 環境変数 `INPUT_RELAY_DEBUG=1` を付けて起動すると receiver / sender 両方で `logging` が DEBUG レベルになり、内部で握り潰している例外のスタックトレースが出力される (障害調査用)。

### 設定ファイルの場所

設定は Windows では `%LOCALAPPDATA%\InputRelay` 配下に保存される。環境変数
`INPUT_RELAY_CONFIG_DIR` を設定した場合はその値をそのまま保存先として使用する。
旧 checkout の `config/` にある下記ファイルは、persistent 側に同名ファイルがない初回
アクセス時だけ自動コピーされる。旧ファイルは変更も削除もされず、persistent 側の既存値が
常に優先される。保存は atomic で、各ファイルには直前の内容のバックアップが最大 5 個保持
される。主ファイルが欠損・破損・object 以外の JSON の場合、最新の有効なバックアップを
自動復旧し、利用可能なバックアップもなければ各 API の既定値を返す。

| ファイル | 内容 |
|---------|------|
| `config.json` | オーバーレイ表示設定全般 (キーボード/レバーレス/コントローラのレイアウト, 履歴設定など) |
| `presets.json` | プリセット (`{ keyboard: {...}, leverless: {...}, controller: {...} }`) |
| `layout_presets.json` | レイアウト+履歴のみのプリセット (同じ 3 タイプ別) |
| `sender_config.json` | sender 接続先・入力機能・リモートオーバーレイ設定・自身の HTTP/Monitor ポート |

---

## 2. Receiver HTTP API (port 8081)

エントリポイント: `OverlayHandler` (`receiver/input_server.py`)。

すべて `application/json; charset=utf-8` で応答。失敗時は `{"error": "<message>"}` と HTTP 400。

### 2.1 設定 (config.json)

#### `GET /api/config`

オーバーレイ表示設定の全体を取得。

レスポンス:
```json
{ /* config.json の中身そのまま。形は overlay 側が解釈する */ }
```

ファイル不在時は `{}` を返す。

#### `POST /api/config`

`config.json` を上書き保存する。保存後、接続中の全ブラウザに `config_change` (kind=`config`) と互換用の `config` メッセージを WebSocket でブロードキャストする。

リクエストボディ:
```json
{ /* 保存したい config 全体 */ }
```

レスポンス:
```json
{ "ok": true }
```

> 注意: 部分更新ではない。GET で取得した JSON を編集して丸ごと POST する。

### 2.2 プリセット (presets.json)

`presets.json` はキー入力デバイスのタイプ別にネストされた構造を持つ:
```json
{
  "keyboard":   { "<preset_name>": { "keyboard":   {...}, "layout": {...}, "inputHistory": {...} } },
  "leverless":  { "<preset_name>": { "leverless":  {...}, "layout": {...}, "inputHistory": {...} } },
  "controller": { "<preset_name>": { "controller": {...}, "layout": {...}, "inputHistory": {...} } }
}
```
旧形式 (フラット) は読み込み時に自動マイグレーションされる。

#### `GET /api/presets`

プリセット一覧 (上記構造) を返す。

#### `POST /api/presets`

プリセットを 1 件保存 (新規 or 上書き)。

リクエストボディ:
```json
{
  "type": "keyboard",          // "keyboard" | "leverless" | "controller" (default: "keyboard")
  "name": "<preset_name>",
  "keyboard": { /* type に対応するキー (このフィールド名は type と一致させる) */ },
  "layout": { /* 任意 */ },
  "inputHistory": { /* 任意 */ }
}
```

レスポンス: `{ "ok": true }`

ブラウザに `config_change` (kind=`presets`, op=`save`, type, name) を通知。

#### `DELETE /api/presets`

リクエストボディ:
```json
{ "type": "keyboard", "name": "<preset_name>" }
```

レスポンス: `{ "ok": true }`

ブラウザに `config_change` (kind=`presets`, op=`delete`, type, name) を通知。存在しないキーを削除しても 200 を返す。

### 2.3 レイアウトプリセット (layout_presets.json)

`presets.json` と同じ 3 タイプ構造。`layout` と `inputHistory` のみ保持し、キー定義は持たない。

#### `GET /api/layout-presets`

レイアウトプリセット一覧を返す。

#### `POST /api/layout-presets`

リクエストボディ:
```json
{
  "type": "keyboard",
  "name": "<preset_name>",
  "layout": { /* 任意 */ },
  "inputHistory": { /* 任意 */ }
}
```

レスポンス: `{ "ok": true }`

ブラウザに `config_change` (kind=`layout_presets`, op=`save`, type, name) を通知。

#### `DELETE /api/layout-presets`

リクエストボディ: `{ "type": "...", "name": "..." }`

レスポンス: `{ "ok": true }`

### 2.4 receiver-local sender 設定 (sender_config.json)

receiver が動く PC の persistent 設定領域にある `sender_config.json` を読み書きする
ファイル API。
通常の 2PC 構成では Main PC と Sub PC は別 workspace のため、**この API で変更する
Sub PC 側ファイルは Main PC の sender プロセスには反映されない**。実行中 sender の
設定変更には Main PC の Sender HTTP API (port 8082) を使う。API 互換性のため endpoint
自体は維持しているが、Main PC sender の live-control API として扱わないこと。

#### `GET /api/sender-config`

`sender_config.json` の中身を返す。ファイル不在時は `{}`。

#### `POST /api/sender-config`

リクエストボディ:
```json
{
  "host": "192.168.1.211",
  "port": 8888,
  "local_name": "Main PC",
  "target_name": "Sub PC",
  "remote_overlay": {
    "enabled": true,
    "position": "top-left"   // top-left/top-center/top-right/middle-left/middle-right/bottom-left/bottom-center/bottom-right
  },
  "http_port": 8082,         // 任意。sender の HTTP API ポート (省略時 8082)
  "monitor_port": 8083       // 任意。sender の Monitor WebSocket ポート (省略時 8083)
}
```

レスポンス: `{ "ok": true }`

ブラウザに `config_change` (kind=`sender_config`, data) を通知 (`data` はマージ後のファイル全体)。

> この POST は全置換ではなく、`host`/`port` の 2 キーだけを既存ファイルへ
> マージする (`_config_io_lock` 配下の 1 read-modify-write transaction、
> `os.replace` による atomic write)。それ以外のキーがボディに含まれていても
> 無視され、既存の値がそのまま残る。同じ workspace で sender も動かす特殊な
> 構成では再起動後に反映されるが、通常の 2PC 構成では Main PC 側ファイルを別途変更する。

### 2.5 強制リフレッシュ

#### `POST /api/refresh`

サーバー側で `config.json` を読み直し、ブラウザに `config_change` (kind=`config`, data) を再送信する。設定ファイルを外部編集した直後にオーバーレイを反映させたい場合に使う。

リクエストボディ: 不要 (空でよい)。

レスポンス: `{ "ok": true }`

### 2.6 リモート操作トグル

receiver 側で sender からの入力イベントを OS 入力として注入するモード。

#### `GET /api/remote-control`

レスポンス:
```json
{ "enabled": false }
```

#### `POST /api/remote-control`

リクエストボディ:
```json
{ "enabled": true }
```

`enabled: false` (無効化) は fail-safe 優先: sender の接続有無や通知の成否に
関わらず receiver 側の注入を即座に無効化し、追跡中の識別子を解放してから
best-effort (非同期・応答を待たない) で sender に通知する。常に
`{ "ok": true, "enabled": false }` を返す。

`enabled: true` (有効化) は sender が「接続済みかつ同期済み」
(接続後、その sender 自身の明示的な `remote_control` 状態メッセージを
受信済み) でなければ拒否する。拒否時は receiver 側の状態を変更せず、
sender へコマンドも送らず、`{ "error": <string> }` を HTTP 409 で返す。
同期済み sender へのコマンド送信自体が失敗した場合も状態を変更せず HTTP 502
を返す。成功時は要求値を含む `{ "ok": true, "enabled": true }` を返すが、
receiver 側の実際の注入有効化は sender からの状態報告 (下記) を受信するまで
行われない (`remote_control_state` ブロードキャストが実際の有効化を伝える)。

副作用 (有効化成功時):
- sender に `{"type":"remote_control","enabled":true}` を WebSocket で通知
  (sender はキーフック suppress を切替, リモート中オーバーレイを表示)。
- sender からの `remote_control` 状態報告受信時に receiver 側の状態を有効化し、
  ブラウザに `{"type":"remote_control_state","enabled":true}` をブロードキャスト。

副作用 (無効化時):
- receiver 側のリモート操作状態を即座に無効化。
- 注入成功時に記録した実 VK / マウスボタン単位の識別子を
  `input_injector.release_identities` で正確に解放 (キー残留防止。表示名からの
  VK 再構成は行わない)。
- ブラウザに `{"type":"remote_control_state","enabled":false}` を WebSocket で
  ブロードキャスト。
- sender へ `{"type":"remote_control","enabled":false}` を best-effort 通知。

sender 接続直後は必ず unsynchronized から始まり、その sender 自身が最初の
`remote_control` メッセージ (ON/OFF いずれか) を送るまで receiver 側の注入は
行われない (stale な有効状態が残っていても無視される)。切断時もこの
同期状態はリセットされ、再接続後は改めて同期が必要。

### 2.7 モード切替指示 (ブラウザ向け)

#### `POST /api/mode-switch`

ブラウザ側オーバーレイの表示モードを切替え、接続中 Main PC sender にも同じ
control message を送る。receiver は最新モードをメモリ中に保持し、新たに接続した
sender にも直ちに送る（永続設定ではない）。

リクエストボディ:
```json
{ "mode": "keyboard" }   // "keyboard" | "leverless" | "controller"
```

レスポンス: `{ "ok": true }`

ブラウザに以下を送信:
```json
{
  "type": "mode_switch",
  "key": "keyboard",
  "source": "system",
  "timestamp": 1712990000.123
}
```

### 2.8 プロセス再起動

#### `DELETE /api/restart`

receiver プロセスを `os.execv` で再起動する。

レスポンス: `{ "ok": true }` (返してから 0.5 秒後に execv)

再入 guard あり: pending 中に重ねて呼んでも新しいスレッドは起動せず、同じ
`{ "ok": true }` を返すだけ。0.5 秒の待機後、execv の前に Remote Control の
注入済み入力を解放 (`_set_rc_state(False)`) し、standalone 実行中なら
`standalone_capture.stop()` も best-effort で呼ぶ (どちらかが失敗しても
もう一方や execv 自体はスキップしない)。execv 自体が失敗した場合は
guard を解除し、次の DELETE で再試行できるようにする。

> メソッドが DELETE である点に注意。

### 2.9 静的ファイルとオーバーレイ

| パス | 内容 |
|------|------|
| `GET /` | `config_gui.html` (設定 GUI) |
| `GET /overlay.html` | OBS 用オーバーレイ (素のまま) |
| `GET /history` | overlay.html を `#key-display` 非表示モードで提供 |
| `GET /input` | overlay.html を `#history` 非表示モードで提供 |
| `GET /mouse-trail` | overlay.html を `#key-display,#history` 非表示モードで提供 |
| `GET /<file>` | `receiver/` 直下の任意ファイル (`.html` `.js` `.css` `.json`) |

`/history` `/input` `/mouse-trail` を要求すると `<head>` に以下を注入する:
```html
<script>window.__DISPLAY_MODE__="<mode>";window.__WS_PORT__="<ws_port>";</script>
<style>...{display:none!important}</style>
```

`config_gui.html` (`GET /`) も同様に `<head>` へ
`<script>window.__WS_PORT__=<ws_port>;</script>` を注入する (receiver プロセスの
実際の WS listen port。デバッグタブの WebSocket 接続はこの値のみを使い、
存在しない/不正な場合のみ 8888 にフォールバックする。receiver-local な
sender-config コピーの port 値は使わない)。

存在しない静的ファイルは 404。

---

## 3. Receiver WebSocket (port 8888)

エントリポイント: `ws_handler` (`receiver/input_server.py`)。パスで処理が分岐する。

### 3.1 `/browser` — ブラウザ向け

OBS のオーバーレイページ・設定 GUI ページが接続するエンドポイント。

接続時にサーバーから 1 件送信:
```json
{ "type": "config", "data": { /* config.json 全体 */ } }
```

その後、サーバー → ブラウザの一方向 push:

| メッセージタイプ | 説明 | 主なフィールド |
|------------------|------|----------------|
| `config` | 互換用。`POST /api/config` 後と `POST /api/refresh` 後に送られる | `data` |
| `config_change` | 拡張通知。設定全般の変更を通知 (詳細は §6) | `kind`, `timestamp`, その他 |
| `mode_switch` | `POST /api/mode-switch` のリレー | `key`, `source`, `timestamp` |
| `remote_control_state` | リモート操作 ON/OFF 状態 | `enabled` |
| `key_down`/`key_up` | sender からの入力イベント (リレー) | `key`, `vk?`, `source`, `timestamp` |
| `mouse_move` | sender からの相対マウス移動 | `dx`, `dy`, `source`, `timestamp` |
| `mouse_scroll` | sender からのマウススクロール | `dx`, `dy`, `source`, `timestamp` |
| `axis_update` | sender からのアナログ軸値 | `axis`, `value`, `source`, `timestamp` |
| `input_reset` | sender 接続の切断 (cleanup) ごとに送信。表示中の押下・方向・軸・afterglow をすべて中立へ戻す指示 (追加フィールドなし、後方互換な新規 type) | なし |

ブラウザ → サーバーのメッセージは `pass` (無視) される。

### 3.2 `/` — sender 向け

sender が接続するエンドポイント (`sender_handler`)。同時接続は 1 つを想定 (`sender_ws` グローバルに最後の接続が入る)。

#### sender → receiver

JSON テキストで以下を送る:

| type | 用途 |
|------|------|
| `key_down`, `key_up`, `mouse_move`, `mouse_scroll`, `axis_update` | 入力イベント。全 `/browser` クライアントにブロードキャストし、リモート操作 ON かつ sender 同期済みのときのみ OS 入力として注入 |
| `remote_control` | sender 自身の明示的な現在状態 (`{"type":"remote_control","enabled":<bool>}`)。接続直後に ON/OFF いずれでも必ず 1 回送信され (通常入力の送信開始より前)、以後はトグルキーでの切替時にも送信される。receiver 側はこれを唯一の「sender 同期済み」根拠として扱い、その sender 接続の受信済み `enabled` 値をそのまま採用する (`false` でも明示的に受理し注入を OFF のまま維持) |

通常 sender 経路へ誤送信された `clipboard_*` は破棄し、OS 入力注入にも
`/browser` にも渡さない。clipboard 本文はこの接続では扱わない。

JSON でない or パース不可能なメッセージは無視される。

#### receiver → sender

| type | 用途 |
|------|------|
| `remote_control` | receiver 側 GUI/API のトグル結果を sender に通知 (`{"type":"remote_control","enabled":<bool>}`)。ON への切替はこの通知が sender へ実際に届いた場合のみ API 成功として扱う |
| `mode_switch` | 現在の表示モード (`key`: `keyboard` / `leverless` / `controller`)。`POST /api/mode-switch` 時と sender 接続直後に送られる。sender は controller/leverless の保存済みデバイス優先設定を解決する |
| `clipboard_offer` | clipboard v1 対応通知。`version: 1` と入力接続ごとの UUID `session` のみを含み、本文は含まない。sender はこの通知を受けた場合だけ `/clipboard` を開く |

新規接続の sender は、その接続自身が上記の状態メッセージを送るまで
unsynchronized 扱いで、たとえ receiver 側に古い ON 状態が残っていても注入は
行われない。切断時にもこの同期状態はリセットされ、リモート操作が ON なら
自動で OFF になる (押下中キーも解放)。再接続後は改めて同期が必要。

### 3.3 `/clipboard` — テキストクリップボード専用

2PC モードだけで利用する双方向 WebSocket。既存 receiver WS と同じ port を使う。
standalone では接続を拒否する。入力接続から `clipboard_offer` を受信した sender だけが
接続し、最初の3秒以内に次の bind を送る。

```json
{"type":"clipboard_bind","version":1,"session":"<input接続のUUID>"}
```

現在の入力接続に属する session だけを受理し、専用接続は1本まで。重複 bind、旧 session、
不正 JSON/型/フィールド、400 KiB を超える message は専用接続だけを終了する。入力接続の
切断・置換時も対応する session、epoch、待機中本文を失効させる。再接続後の初期状態は OFF。

| type | 方向 | 必須フィールド | 用途 |
|------|------|----------------|------|
| `clipboard_bind` | Main → Sub | `version`, `session` | 入力接続との関連付け |
| `clipboard_set` | Main → Sub | `request_id`, `enabled` | 明示的な ON/OFF 要求 |
| `clipboard_state` | Sub → Main | `request_id`, `enabled`, `epoch`, `reason` | 確定状態。初回/OFF は `epoch:null`、ON ごとに新 UUID |
| `clipboard_propose` | Main → Sub | `epoch`, `origin_seq`, `text` | Main のローカルコピー提案 |
| `clipboard_update` | Sub → Main | `epoch`, `revision`, `origin`, `origin_seq`, `text` | receiver が受付順を確定した更新 |
| `clipboard_error` | 双方向 | `epoch`, `code` | 本文を含まない失敗通知 |

整数は bool を受け付けない非負整数。`origin` は `main|sub`、`reason`/`code` は
`user|disconnected|unavailable|busy|too_large|invalid_data|write_failed|worker_failed`。
`too_large` は共有 ON を維持し、それ以外の worker/protocol 失敗は共有を停止する。

`text` は非空の有効な Unicode 文字列で、埋め込み NUL と孤立 surrogate を許可せず、
UTF-8 で 65,536 bytes 以下。receiver が両端の提案を受付順に revision へ確定するため、
同時コピーは receiver が最後に受理した内容へ収束する。本文送信は各端最大10回/秒、
ローカル読出し・本文送信・OS 適用の待機枠はそれぞれ最新1件に制限する。

---

## 4. Sender HTTP API (port 8082)

エントリポイント: `SenderHTTPHandler` (`sender/http_api.py`)。CORS は全許可 (`Access-Control-Allow-Origin: *`).

### 4.1 `GET /` , `GET /index.html`

`sender_gui.html` を返す (設定 GUI)。

### 4.2 `GET /api/config`

現在メモリに乗っている sender 設定を返す (`sender_config.json` の内容 + デフォルトマージ済み)。

レスポンス例:
```json
{
  "host": "192.168.1.211",
  "port": 8888,
  "gamepad_enabled": true,
  "raw_mouse_enabled": true,
  "local_name": "Main PC",
  "target_name": "Sub PC",
  "remote_overlay": { "enabled": true, "position": "top-left" },
  "http_port": 8082,
  "monitor_port": 8083,
  "mode_device_preferences": {
    "controller": { "guid": "..." },
    "leverless": null
  }
}
```

> 旧 `toggleKey` キー (F12 モード切替、撤去済み) はデフォルトから削除済み。
> 既存の `sender_config.json` に残っていても無視される。
> `http_port` / `monitor_port` はデフォルトにも含まれる読み取り専用の起動時設定
> (§4.3) で、値が欠落/不正な場合は 1-65535 の範囲チェック付きで既定 8082/8083 に
> 正規化される。

### 4.3 `POST /api/config`

sender 設定を更新する。受け付けるキー: `host`, `port`, `gamepad_enabled`, `raw_mouse_enabled`, `local_name`, `target_name`, `remote_overlay.enabled`, `remote_overlay.position`。それ以外のキーは無視される (`gamepad_enabled` / `raw_mouse_enabled` は保存のみで、キャプチャスレッドの起動/停止への反映は sender 再起動時)。sender 自身の `http_port` / `monitor_port` はこの API では変更できないため、Main PC 側の `sender_config.json` を直接編集して `POST /api/restart` する。

リクエストボディ例:
```json
{
  "host": "192.168.1.211",
  "port": 8888,
  "local_name": "Main PC",
  "target_name": "Sub PC",
  "remote_overlay": { "enabled": true, "position": "top-right" }
}
```

レスポンス: `{ "ok": true }`

副作用: `host` / `port` が変化した場合のみ受信側への WebSocket 接続を再接続する。

### 4.4 `GET /api/status`

sender の現在状態。

レスポンス:
```json
{
  "ws_status": "connected",          // "connecting" | "connected" | "disconnected"
  "host": "192.168.1.211",
  "port": 8888,
  "selected_controller": 0,
  "remote_mode": false,
  "clipboard": {
    "state": "off",                    // "unavailable" | "off" | "enabling" | "on" | "disabling"
    "enabled": false,                   // state == "on"
    "reason": "user"
  },
  "last_kbd_mouse_ts": 1712990000.1, // キーボード/マウス最終入力の UNIX 秒 (未観測は 0.0)
  "last_gamepad_ts": 1712990000.1,   // ゲームパッド最終入力の UNIX 秒 (未観測は 0.0)
  "server_time": 1712990000.2        // sender 側の現在時刻 (ts との差分計算用)
}
```

`last_*_ts` は secretary-bot 等が Main/Sub どちらを操作中かを判定するためのフィールド。リモートモード中のキーボード/マウス入力は Sub PC 側で消費されるが、ゲームパッドは物理的に Main PC 接続のため常に Main 側操作を意味する。
`clipboard` には状態だけを載せ、本文、session、epoch、接続識別子は含めない。

### 4.5 `GET /api/controllers`

検出済みコントローラ一覧。

レスポンス:
```json
{
  "enabled": true,                   // gamepad_enabled 設定値
  "controllers": [ { "id": 0, "name": "Xbox Controller", /* ... */ } ],
  "selected": 0
}
```

`controllers` の中身は pygame の joystick 情報 (id, name 等) と、保存に使う
`identity` を含む。`identity` は GUID が取得できる場合 `{ "guid": "..." }`、
取得できない場合は `{ "name", "buttons", "axes", "hats" }` の署名になる。

### 4.6 `POST /api/select-controller`

リクエストボディ:
```json
{ "id": 0 }
```

レスポンス:
```json
{ "ok": true, "id": 0, "name": "Xbox Controller" }
```

### 4.7 `POST /api/refresh-controllers`

コントローラを再スキャン (`input_common/gamepad.py` の
`Gamepad.request_refresh()` に再スキャン要求を送り、約 0.3 秒待ってから結果を返す)。

レスポンス:
```json
{
  "controllers": [ /* 同上 */ ],
  "selected": 0,
  "count": 1
}
```

### 4.8 `POST /api/mode-device-preference`

controller / leverless 表示モード用の Main PC ローカルなデバイス優先設定を保存・解除する。

```json
{ "mode": "controller", "device": { "guid": "..." } }
```

`mode` は `controller` または `leverless` のみ。`device` は `GET /api/controllers` の
`identity` をそのまま渡すか、`null` を渡して解除する。不正な mode / identity は 400。
保存済み identity が scan 後に見つからない、または fallback 署名が複数に一致する場合、
sender は別のデバイスへフォールバックせず入力を中立化する。設定を解除した場合は従来の
手動選択へ戻る。

### 4.9 `POST /api/restart`

sender プロセスを `os.execv` で再起動。

レスポンス: `{ "ok": true, "message": "Restarting..." }` (0.5 秒後に execv)

---

## 5. Sender Monitor WebSocket (port 8083)

エントリポイント: `MonitorServer` (`sender/monitor_ws.py`)。

- 任意のクライアントが接続できる。**サーバー → クライアントの一方向ブロードキャスト**。クライアントから送られたメッセージは破棄される。
- sender がキャプチャした全入力イベント (キーボード, マウスクリック/移動/スクロール, ゲームパッドボタン/ハット/軸) を実時間で配信する。
- リモート操作トグル時には `remote_control_state` イベントも配信される。
- clipboard 本文と `clipboard_*` 制御 message は配信されない。

イベント形式は §6 と同じ。

---

## 6. イベントフォーマット

### 6.1 共通フィールド

| フィールド | 型 | 説明 |
|------------|----|------|
| `type` | string | イベント種別 |
| `timestamp` | number | UNIX 秒 (float) |
| `source` | string | `"keyboard"` / `"mouse"` / `"gamepad"` / `"system"` |

### 6.2 入力イベント

#### `key_down` / `key_up`

```json
{
  "type": "key_down",
  "key": "a",                   // 正規化済みキー名 ("shift", "ctrl", "alt", "f12", "btn_0", "hat_0_up", "axis_0_neg", "mouse_left" ...)
  "vk": 65,                      // 任意 (キーボード入力のみ。pynput の Virtual Key Code)
  "source": "keyboard",
  "timestamp": 1712990000.123
}
```

`key` の命名:
- 文字キー: VK で正規化した小文字英数字 (`a`〜`z`, `0`〜`9`)。修飾キーで `!` 等にならないよう vk から復元。
- 修飾キー: `shift` / `ctrl` / `alt` (左右の区別なし)
- その他特殊キー: pynput の `Key.<name>` の `name` (例 `f12`, `space`)
- マウスボタン: `mouse_left` / `mouse_right` / `mouse_middle` / `mouse_x1` / `mouse_x2` (source=`mouse`)
- ゲームパッドボタン: `btn_<index>` (source=`gamepad`)
- ハット: `hat_<i>_left` / `hat_<i>_right` / `hat_<i>_up` / `hat_<i>_down`
- 軸 (閾値判定): `axis_<i>_neg` / `axis_<i>_pos` (deadzone 0.5)
- フォールバック: `vk_<vkcode>` (IME キー等)

#### `mouse_move`

```json
{
  "type": "mouse_move",
  "dx": 12, "dy": -3,
  "source": "mouse",
  "timestamp": 1712990000.123
}
```

Windows Raw Input API で取得した相対移動。約 60Hz でスロットル送信 (蓄積デルタを送る)。

#### `mouse_scroll`

```json
{
  "type": "mouse_scroll",
  "dx": 0, "dy": 1,
  "source": "mouse",
  "timestamp": 1712990000.123
}
```

Overlay 表示では、縦スクロールを一瞬だけ押下される表示用キーとして扱う:
`dy > 0` は `mouse_scroll_up`、`dy < 0` は `mouse_scroll_down`。

#### `axis_update`

```json
{
  "type": "axis_update",
  "axis": 0,
  "value": 0.732,                // -1.0 .. 1.0 (3 桁丸め)
  "source": "gamepad",
  "timestamp": 1712990000.123
}
```

連続値の軸更新。`key_down`/`key_up` の閾値版 (`axis_<i>_neg/pos`) と同時に送られる。

### 6.3 制御イベント

#### `remote_control` (sender ↔ receiver)

```json
{ "type": "remote_control", "enabled": true }
```

#### `remote_control_state` (server → ブラウザ / monitor)

```json
{ "type": "remote_control_state", "enabled": true,
  "source": "system", "timestamp": 1712990000.123 }
```

(monitor 配信版のみ source/timestamp が付く。`/browser` 配信版は enabled のみ)

#### `mode_switch` (server → ブラウザ)

```json
{ "type": "mode_switch", "key": "keyboard",
  "source": "system", "timestamp": 1712990000.123 }
```

#### `input_reset` (server → ブラウザ)

```json
{ "type": "input_reset" }
```

sender の WebSocket ハンドラ (`sender_handler`) の `finally` cleanup で、
接続 1 回の切断ごとに必ず 1 件ブロードキャストされる (Remote Control が
無効のままでも送られる)。overlay.html はこれを受けて `displayDelayTimers`・
`afterglowTimers` を即座に cancel し、`pressedKeys`・`dirState`・`axisState`・
アクティブ/afterglow の DOM クラス・コントローラーのスティック/トリガー表示・
マウストレイルの蓄積点を中立へ戻す。レイアウト再構築 (`buildLayout`) や
履歴エントリの追加は行わない。

### 6.4 設定変更通知 `config_change` (server → ブラウザ)

`POST /api/config` `POST /api/presets` `DELETE /api/presets` `POST /api/layout-presets` `DELETE /api/layout-presets` `POST /api/sender-config` `POST /api/refresh` の後に push される拡張通知。

共通:
```json
{ "type": "config_change", "kind": "<kind>", "timestamp": 1712990000.123, /* extras */ }
```

| kind | extras |
|------|--------|
| `config` | `data`: 保存後の config 全体 |
| `sender_config` | `data`: 保存後の sender_config 全体 |
| `presets` | `type`: "keyboard"/"leverless"/"controller", `name`, `op`: "save"/"delete" |
| `layout_presets` | `type`, `name`, `op`: "save"/"delete" |

> 互換性のため、kind=`config` のときは `{"type":"config","data":...}` も別途送られる (旧 overlay.html リスナ向け)。

---

## 7. 外部クライアント向けガイドライン

### 7.1 設定変更を反映させる

1. `POST /api/config` (または presets/layout-presets) で書き込み。
2. receiver 側で自動的に `/browser` WebSocket 経由でブラウザに通知されるため、追加操作は不要。
3. Main PC sender の live 設定は `POST http://<sender>:8082/api/config` で変更する。
   `http_port` / `monitor_port` など同 API が受け付けない項目は Main PC 側の
   persistent 設定領域にある `sender_config.json` を変更して sender を再起動する。receiver の
   `/api/sender-config` は Sub PC ローカルのファイル API であり、Main PC には転送しない。

### 7.2 変更を監視する

- `ws://<receiver>:8888/browser` に接続し、`config_change` メッセージを受信する。
- 接続直後に最新 `config` が必ず 1 件届く (初期同期用)。
- 詳細データが必要なら kind に応じて `GET /api/config` `/api/presets` `/api/layout-presets` `/api/sender-config` で取り直す (presets/layout_presets の通知には差分しか含まれない)。

### 7.3 入力を監視する

- ブラウザ向け: `ws://<receiver>:8888/browser` に届く `key_down` `key_up` `mouse_move` `mouse_scroll` `axis_update` を観測する。受信元は sender 1 つ + (standalone モード時) ローカルキャプチャ。
- sender 直接観測: `ws://<sender>:8083/` に接続。sender 側で発生したすべての入力イベントが流れる (receiver 接続有無に関わらず動作)。

### 7.4 リモート操作の制御

- 状態取得: `GET http://<receiver>:8081/api/remote-control`
- 切替: `POST http://<receiver>:8081/api/remote-control` body `{"enabled": true|false}`
- 切替に伴い sender 側でキーフック suppress とオーバーレイ表示が切り替わる。
- sender との WebSocket が切れた瞬間に強制 OFF される (receiver 側の安全策)。

### 7.5 注意点

- 認証ヘッダーは無い。LAN セグメント外からのアクセスを許可しないこと。
- すべての書き込み API は楽観的: バリデーションは最小限。不正な JSON は HTTP 400、構造不備は overlay 側で表示崩れになる可能性あり。
- `POST /api/config` はマージではなく**全置換**。GET → 編集 → POST のフローを徹底すること。
- `DELETE /api/restart` (receiver) と `POST /api/restart` (sender) でメソッドが異なる点に注意。
- clipboard 共有は Main PC の `Shift + Scroll Lock` だけで切り替える。HTTP POST/GUI toggle はなく、`GET /api/status` で状態のみ確認できる。
- clipboard 共有は信頼済み private LAN / 無認証という既存境界を継承する。session/epoch は接続世代の取り違え防止用で、認証や暗号化ではない。
