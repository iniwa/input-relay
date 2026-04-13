# input-relay JSON API リファレンス

> 調査日: 2026-04-13
> 対象: `receiver/input_server.py`, `sender/input_sender.py`

外部管理ツール (secretary-bot 等) から LAN 経由で input-relay の設定 CRUD と状態取得を行うための仕様。実装と乖離しないよう、実ソースから確認した挙動のみを記載する。

---

## 1. 概要

input-relay は以下の 2 プロセスで構成される (単独モードでは receiver のみ)。

| プロセス | 役割 | HTTP | WebSocket |
|---------|------|------|-----------|
| receiver | OBS オーバーレイ表示, 設定 GUI, sender 入力受信, リモート操作のホスト | 8081 (既定) | 8888 (既定) |
| sender | キーボード/マウス/ゲームパッド入力をキャプチャして receiver に送信 | 8082 (既定) | 8083 (監視用, 既定) |

- すべて `0.0.0.0` で listen し、LAN 公開前提。
- **認証はない。** LAN 内信頼ゾーンでのみ使用すること。
- HTTP は `ThreadingHTTPServer` で動くため複数クライアントから同時アクセス可能。設定書き込みは内部で単一ロック (`_config_io_lock`) で直列化される。
- ポートはコマンドライン引数 (receiver) または `sender_config.json` (sender) で変更可能。

### 設定ファイルの場所

receiver から見て `../config/` 配下:

| ファイル | 内容 |
|---------|------|
| `config/config.json` | オーバーレイ表示設定全般 (キーボード/レバーレス/コントローラのレイアウト, 履歴設定など) |
| `config/presets.json` | プリセット (`{ keyboard: {...}, leverless: {...}, controller: {...} }`) |
| `config/layout_presets.json` | レイアウト+履歴のみのプリセット (同じ 3 タイプ別) |
| `config/sender_config.json` | sender 接続先・トグルキー・リモートオーバーレイ設定 |

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

### 2.4 sender 設定 (sender_config.json)

receiver 経由で sender 側設定ファイルを読み書きする。**sender プロセス自体には反映されない** (sender はファイル監視していない)。反映には sender の `POST /api/restart` か OS 側からの再起動が必要。

#### `GET /api/sender-config`

`sender_config.json` の中身を返す。ファイル不在時は `{}`。

#### `POST /api/sender-config`

リクエストボディ:
```json
{
  "host": "192.168.1.211",
  "port": 8888,
  "toggleKey": "f12",
  "local_name": "Main PC",
  "target_name": "Sub PC",
  "remote_overlay": {
    "enabled": true,
    "position": "top-left"   // top-left/top-center/top-right/middle-left/middle-right/bottom-left/bottom-center/bottom-right
  }
}
```

レスポンス: `{ "ok": true }`

ブラウザに `config_change` (kind=`sender_config`, data) を通知。

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

レスポンス:
```json
{ "ok": true, "enabled": true }
```

副作用:
- receiver 側のリモート操作状態を切替え。
- sender に `{"type":"remote_control","enabled":<bool>}` を WebSocket で通知 (sender はキーフック suppress を切替, リモート中オーバーレイを表示)。
- ブラウザに `{"type":"remote_control_state","enabled":<bool>}` を WebSocket でブロードキャスト。
- OFF にすると、押下中として記録されている全キーを `release_all` で解放 (キー残留防止)。

### 2.7 モード切替指示 (ブラウザ向け)

#### `POST /api/mode-switch`

ブラウザ側オーバーレイの表示モードを切替えるための push のみ行う (サーバー側に状態を保存しない)。

リクエストボディ:
```json
{ "mode": "keyboard" }   // 任意の文字列。ブラウザ側で解釈
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

ブラウザ → サーバーのメッセージは `pass` (無視) される。

### 3.2 `/` (それ以外) — sender 向け

sender が接続するエンドポイント (`sender_handler`)。同時接続は 1 つを想定 (`sender_ws` グローバルに最後の接続が入る)。

#### sender → receiver

JSON テキストで以下を送る:

| type | 用途 |
|------|------|
| `key_down`, `key_up`, `mouse_move`, `mouse_scroll`, `axis_update` | 入力イベント。全 `/browser` クライアントにブロードキャストし、リモート操作 ON のときは OS 入力として注入 |
| `remote_control` | sender 側のトグルキーから状態切替 (`{"type":"remote_control","enabled":<bool>}`) |

JSON でない or パース不可能なメッセージは無視される。

#### receiver → sender

| type | 用途 |
|------|------|
| `remote_control` | receiver 側 GUI/API のトグル結果を sender に通知 (`{"type":"remote_control","enabled":<bool>}`) |

切断時、リモート操作が ON なら自動で OFF になる (押下中キーも解放)。

---

## 4. Sender HTTP API (port 8082)

エントリポイント: `SenderHTTPHandler` (`sender/input_sender.py`)。CORS は全許可 (`Access-Control-Allow-Origin: *`).

### 4.1 `GET /` , `GET /index.html`

`sender_gui.html` を返す (設定 GUI)。

### 4.2 `GET /api/config`

現在メモリに乗っている sender 設定を返す (`sender_config.json` の内容 + デフォルトマージ済み)。

レスポンス例:
```json
{
  "host": "192.168.1.211",
  "port": 8888,
  "toggleKey": "f12",
  "local_name": "Main PC",
  "target_name": "Sub PC",
  "remote_overlay": { "enabled": true, "position": "top-left" }
}
```

### 4.3 `POST /api/config`

sender 設定を更新する。受け付けるキー: `host`, `port`, `local_name`, `target_name`, `remote_overlay.enabled`, `remote_overlay.position`。それ以外のキーは無視される。

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
  "remote_mode": false
}
```

### 4.5 `GET /api/controllers`

検出済みコントローラ一覧。

レスポンス:
```json
{
  "controllers": [ { "id": 0, "name": "Xbox Controller", /* ... */ } ],
  "selected": 0
}
```

`controllers` の中身は pygame の joystick 情報 (id, name 等) 。

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

コントローラを再スキャン (`gamepad_loop` に再スキャン要求を送り、約 0.3 秒待ってから結果を返す)。

レスポンス:
```json
{
  "controllers": [ /* 同上 */ ],
  "selected": 0,
  "count": 1
}
```

### 4.8 `POST /api/restart`

sender プロセスを `os.execv` で再起動。

レスポンス: `{ "ok": true, "message": "Restarting..." }` (0.5 秒後に execv)

---

## 5. Sender Monitor WebSocket (port 8083)

エントリポイント: `monitor_handler` (`sender/input_sender.py`)。

- 任意のクライアントが接続できる。**サーバー → クライアントの一方向ブロードキャスト**。クライアントから送られたメッセージは破棄される。
- sender がキャプチャした全入力イベント (キーボード, マウスクリック/移動/スクロール, ゲームパッドボタン/ハット/軸) を実時間で配信する。
- リモート操作トグル時には `remote_control_state` イベントも配信される。

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

1. `POST /api/config` (または presets/layout-presets/sender-config) で書き込み。
2. receiver 側で自動的に `/browser` WebSocket 経由でブラウザに通知されるため、追加操作は不要。
3. ただし sender 設定 (`sender_config.json`) を変えても sender プロセスには反映されない。`POST http://<sender>:8082/api/restart` で sender を再起動する。

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
