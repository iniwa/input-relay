# Input Display (OBS Overlay)

キーボード/ゲームパッド入力を OBS のブラウザソースとして表示するツール。
SF6 配信向けに、レバーレス/Hitbox レイアウトと入力履歴表示に対応。

## 動作モード

### 単独モード（1PC）

1台の PC でキャプチャから表示まで完結する。

```
PC (standalone)
┌────────────────────────────────────┐
│ input_server --standalone          │
│   - keyboard / mouse / gamepad     │
│   - HTTP server → overlay.html     │
│     (OBS Browser Source)           │
└────────────────────────────────────┘
```

### 2PC モード（Main PC → Sub PC）

Main PC の入力を Sub PC に転送して表示する。

```
Main PC (sender)              Sub PC (receiver)
┌──────────────┐   WebSocket   ┌──────────────────────────┐
│ input_sender │ ───────────→  │ input_server             │
│ - keyboard   │   LAN         │   ↓                      │
│ - mouse      │               │ HTTP server → overlay.html│
│ - gamepad    │               │ (OBS Browser Source)     │
└──────────────┘               └──────────────────────────┘
```

## セットアップ

### 単独モード（1PC）

`start_standalone.bat` をダブルクリック。

- 自動で `git pull`、依存パッケージのインストールを行い、サーバーを起動
- 設定 GUI がブラウザで自動的に開く

コマンドラインの場合:

```bash
python receiver/input_server.py --http-port 8081 --standalone
```

### 2PC モード

両 PC でリポジトリをクローンした上で:

1. **Sub PC**: `start_receiver.bat` をダブルクリック
   - ファイアウォール設定、`git pull`、依存パッケージのインストール後にサーバーを起動
   - 初回のみ管理者権限での実行が必要（ファイアウォールルール追加のため）

2. **Main PC**: `start_sender.bat` をダブルクリック
   - `git pull`、依存パッケージのインストール後に送信を開始

### OBS にオーバーレイを追加

1. OBS のソースに「ブラウザ」を追加
2. URL: 下記 URL 一覧から用途に合わせて選択
3. 幅: 700、高さ: 200（お好みで調整）
4. 「カスタム CSS」は空にする
5. 透過背景で表示される

## URL 一覧

サーバー起動後、以下の URL が利用可能（デフォルトポート `8081`）。

| URL | 内容 |
|-----|------|
| `http://localhost:8081/` | 設定 GUI |
| `http://localhost:8081/overlay.html` | キー表示 + 入力履歴（両方表示） |
| `http://localhost:8081/input` | キー表示のみ（履歴なし） |
| `http://localhost:8081/history` | 入力履歴のみ（キー表示なし） |
| `http://localhost:8081/mouse-trail` | マウストレイル表示 |

2PC モードで外部からアクセスする場合は `localhost` を Receiver PC の IP に置き換え。

## 設定 GUI

`http://localhost:8081/` にアクセスすると設定画面が開く。

| タブ | 内容 |
|------|------|
| **Sender** | 接続先 IP、ポート、モード切替キー |
| **Keyboard** | キーボードモードで表示するキーの追加・削除・位置変更 |
| **Leverless** | 方向ボタン・アクションボタンのマッピング編集 |
| **Controller** | コントローラーレイアウトの設定 |
| **入力履歴** | 最大表示数、アイドルタイムアウト |
| **デバッグ** | 送信中のボタンをリアルタイム確認 |

ページ下部にリアルタイムプレビューあり。

## モード切替

- **F12** キー（デフォルト）で手動切替: `keyboard` → `leverless` → `controller` の順に循環
- 切替キーは設定 GUI の Sender タブで変更可能
- 設定 GUI のプレビュー上部のボタンからも切替可能

## リモートコントロール（2PC モードのみ）

Scroll Lock キーで Main PC の入力を Sub PC に注入するリモコンモードを切り替え可能。
設定 GUI からもトグルできる。

リモートモード中は Main PC の画面端に「`target_name` を操作中」という半透明オーバーレイが
表示され、ゲームウィンドウからフォーカスを奪うことでキーボード・マウス操作がゲームに
届かないようにする。解除するとオーバーレイが破棄され、Z オーダーで元のウィンドウが
自動的に前面へ戻る。

### 既知の制限

**Raw Input をバックグラウンドでも受け取るゲーム**（例: Escape from Tarkov のレイド中など、
`RIDEV_INPUTSINK` 相当を使うタイトル）では、Windows のアーキテクチャ上、マウス移動を
完全に遮断することができない。この種のゲームではリモートモードに入る前に一時停止するか
メニューを開くこと。

一方、HoYoverse 系（Zenless Zone Zero、アークナイツ エンドフィールド等）や Overwatch 2 など、
フォーカス喪失時に入力処理を停止するゲームでは期待通り動作する。

また、排他フルスクリーンのゲームは topmost ウィンドウとの相性が悪いので、ボーダーレス
ウィンドウモードでの運用を推奨する。

### 設定項目（`sender_config.json`）

| キー | 説明 | 既定値 |
|------|------|--------|
| `local_name` | このPC の表示名（オーバーレイ補足用） | `""` |
| `target_name` | 操作対象 PC の表示名（オーバーレイ本文用） | `"Sub PC"` |
| `remote_overlay.enabled` | オーバーレイ表示の有効化 | `true` |
| `remote_overlay.position` | オーバーレイ位置（8 方向） | `"top-left"` |

`position` に指定可能な値:
`top-left` / `top-center` / `top-right` /
`middle-left` / `middle-right` /
`bottom-left` / `bottom-center` / `bottom-right`

## ファイル構成

```
├── start_standalone.bat              # 単独モード起動用
├── start_sender.bat                  # 2PC: Main PC 起動用
├── start_receiver.bat                # 2PC: Sub PC 起動用
├── config/
│   ├── config.json                   # キーマッピング設定
│   ├── sender_config.json            # Sender 接続設定
│   ├── presets.json                  # 表示プリセット
│   ├── layout_presets.json           # レイアウトプリセット
│   ├── *.example.json                # 設定テンプレート
├── sender/
│   ├── input_sender.py               # 入力キャプチャ + WebSocket 送信
│   └── sender_gui.html               # Sender 設定画面
├── receiver/
│   ├── input_server.py               # WebSocket サーバー + HTTP サーバー
│   ├── input_injector.py             # リモコン用入力注入
│   ├── standalone_capture.py         # 単独モード用入力キャプチャ
│   ├── overlay.html                  # OBS 用オーバーレイ
│   └── config_gui.html               # Web 設定画面
└── startup/
    ├── setup_startup_sender.bat      # Sender 自動起動登録
    └── setup_startup_receiver.bat    # Receiver 自動起動登録
```

設定ファイルは `config/` に保存され、初回起動時に自動生成される。
手動で作成する場合は `.example.json` をコピーしてリネーム。

## 依存パッケージ

bat ファイルが自動インストールするため手動でのインストールは不要。

| モード | パッケージ |
|--------|-----------|
| 単独モード | `websockets`, `pynput`, (`pygame`: ゲームパッド使用時) |
| 2PC Sender | `websockets`, `pynput`, `pygame` |
| 2PC Receiver | `websockets` |
