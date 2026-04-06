# Input Display: Main PC → Sub PC (OBS Overlay)

Main PC のキーボード/ゲームパッド入力を Sub PC に送信し、OBS のブラウザソースとして表示するツール。
SF6 配信向けに、レバーレス/Hitbox レイアウトと入力履歴表示に対応。

## 構成

```
Main PC (sender)              Sub PC (receiver + display)
┌──────────────┐   WebSocket   ┌──────────────────────────┐
│ input_sender │ ───────────→  │ input_server             │
│   (Python)   │   LAN         │   ↓                      │
│ - keyboard   │               │ HTTP server → overlay.html│
│ - gamepad    │               │ (OBS Browser Source)     │
└──────────────┘               └──────────────────────────┘
```

## セットアップ

### 1. 両 PC でリポジトリをクローン

```bash
git clone <this-repo-url>
```

### 2. Sub PC (Receiver) を起動

`start_receiver.bat` をダブルクリック。

- 自動で `git pull`、依存パッケージのインストール、ファイアウォール設定を行い、サーバーを起動
- 設定 GUI がブラウザで自動的に開く
- 初回のみ管理者権限での実行が必要（ファイアウォールルール追加のため）

### 3. Main PC (Sender) を起動

`start_sender.bat` をダブルクリック。

- 自動で `git pull`、依存パッケージのインストールを行い、送信を開始
- 設定 GUI がブラウザで開く（Receiver が起動済みの場合）

### 4. OBS にオーバーレイを追加

1. OBS のソースに「ブラウザ」を追加
2. URL: 下記 URL 一覧から用途に合わせて選択
3. 幅: 700、高さ: 200（お好みで調整）
4. 「カスタム CSS」は空にする
5. 透過背景で表示される

## URL 一覧

Sub PC で起動後、以下の URL が利用可能（ポート `8081`）。

| URL | 内容 |
|-----|------|
| `http://localhost:8081/` | 設定 GUI |
| `http://localhost:8081/overlay.html` | キー表示 + 入力履歴（両方表示） |
| `http://localhost:8081/input` | キー表示のみ（履歴なし） |
| `http://localhost:8081/history` | 入力履歴のみ（キー表示なし） |

OBS のブラウザソースや外部からのアクセスには Receiver PC の IP アドレスを使用。
Sub PC 上のブラウザからアクセスする場合は `localhost` に置き換え可能。

## 1台で動作確認

Receiver と Sender を同じ PC で両方起動すれば、1台で完結して表示確認ができる。
Sender の接続先を `localhost` に設定するだけで OK。

## 設定 GUI

`http://<receiver-ip>:8081/` にアクセスすると設定画面が開く。

| タブ | 内容 |
|------|------|
| **Sender** | 接続先 IP、ポート、モード切替キー |
| **Keyboard** | キーボードモードで表示するキーの追加・削除・位置変更 |
| **Leverless** | 方向ボタン・アクションボタンのマッピング編集 |
| **入力履歴** | 最大表示数、アイドルタイムアウト |
| **デバッグ** | Main PC から送信中のボタンをリアルタイム確認 |

ページ下部にリアルタイムプレビューがあり、クリックやキーボード入力で表示を確認できる。

## モード切替

- ゲームパッドを接続してボタンを押すと、オーバーレイが自動的にレバーレスモードに切り替わる
- **F12** キー（デフォルト）で手動切替も可能：`keyboard` → `leverless` → `controller` の順に循環
- 切替キーは設定 GUI の Sender タブで変更可能

## ファイル構成

```
├── start_sender.bat                  # Main PC 起動用
├── start_receiver.bat                # Sub PC 起動用
├── sender/
│   ├── input_sender.py               # 入力キャプチャ + WebSocket 送信
│   └── sender_config.example.json    # Sender 接続設定（テンプレート）
└── receiver/
    ├── input_server.py               # WebSocket サーバー + HTTP サーバー
    ├── overlay.html                  # OBS 用オーバーレイ
    ├── config.example.json           # キーマッピング設定（テンプレート）
    └── config_gui.html               # Web 設定画面
```

設定ファイル（`sender_config.json`, `config.json` 等）は初回起動時に自動生成されます。
手動で作成する場合は `.example.json` をコピーしてリネームしてください。

## 依存パッケージ

bat ファイルが自動インストールするため手動でのインストールは不要。

- **Main PC**: `pynput`, `websockets`, `pygame`
- **Sub PC**: `websockets`
