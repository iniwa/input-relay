# CLAUDE_ja.md - Windows 開発環境（詳細版）

> AI が実際に読む指示書は `CLAUDE.md`（英語・簡潔版）です。このファイルは人間向けの詳細リファレンスです。

---

## コミュニケーション規約

- コードは軽量・効率的なものを基本とする
- **対象 PC が指定されていない場合、メイン PC かサブ PC かを確認してから作業する**

---

## 実行環境

> メイン PC とサブ PC の 2 台構成。

### メイン PC

| 項目 | 詳細 |
|------|------|
| CPU | AMD Ryzen 7 9800X3D |
| GPU | NVIDIA RTX 4080 — CUDA 対応 |
| RAM | 48GB |
| OS | Windows 11 |
| IP | 192.168.1.210 |

### サブ PC

| 項目 | 詳細 |
|------|------|
| CPU | AMD Ryzen 9 5950X（16コア / 32スレッド） |
| GPU | NVIDIA RTX 5060 Ti（VRAM 16GB, CUDA Compute 8.9 / sm_89） |
| RAM | 64GB |
| OS | Windows 11 |
| IP | 192.168.1.211 |

---

## AI / ML 開発

### 目的

- AI / ML ツールをローカルで動かすことが主目的
- 配布・パッケージングは不要（インストーラ・exe 化は特別な要求があるときのみ）

### GPU / CUDA

- 両 PC とも CUDA 利用可能
- GPU 対応ライブラリを積極的に使用する
- GPU が不要なタスクは CPU 処理に留め、VRAM を節約すること

### ライブラリ選定の指針

- **PyTorch**: `torch` + CUDA 対応ビルド（[公式インストーラ](https://pytorch.org/get-started/locally/) で CUDA バージョンを指定）
- **推論**: `transformers`, `llama-cpp-python`（CUDA ビルド）, `onnxruntime-gpu` など
- VRAM に収まるモデルサイズを意識する（量子化モデルの活用も検討）

### 言語・スタック

- ML タスクは Python をデフォルトとする
- GUI が必要な場合の選択肢:
  - `tkinter` — 標準ライブラリ、軽量
  - `Gradio` / `Streamlit` — Web UI、ML ツールとの相性が良い
  - `PyQt6` — 本格的な GUI が必要な場合

---

## 一般ツール開発

### 目的

- ユーティリティスクリプト、自動化、汎用ツールの作成

### 言語・スタック

- **言語は固定しない**。用途に応じて最もシンプルなものを選択する
- GUI が必要な場合は上記と同じ選択肢

---

## 共通：Python 環境管理

| ツール | 推奨度 | 備考 |
|--------|--------|------|
| グローバル | ◎ | 仮想環境なしが基本。依存競合がなければこれ |
| `uv` | ○ | 競合時に使用。高速・軽量 |
| `venv` + `pip` | ○ | シンプルで確実 |
| `conda` | △ | 必要な依存関係が conda のみの場合に限定 |

---

## コードスタイル

- シンプルで読みやすいコードを優先する
- 単純なタスクに重厚なフレームワークは使わない
- 抽象化・汎用化はその場で必要になってから行う
- 依存ライブラリは最小限に留める（標準ライブラリで済むならそうする）
- CI/CD・パッケージングは特別な要求があるときのみ対応する

---

## プロジェクト構成の例

```
tool-name/
├── .claudeignore
├── README.md
├── requirements.txt      # または pyproject.toml (uv)
├── main.py               # エントリポイント
└── src/                  # アプリケーションコード
```

---

## .claudeignore テンプレート

```gitignore
# Git 内部ファイル
.git/

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
*.egg-info/

# モデルファイル（大容量のため除外）
*.bin
*.safetensors
*.gguf
*.pt
*.pth
models/
weights/

# ログ・一時ファイル
*.log
*.tmp
*.temp
logs/
tmp/

# バックアップ
*.bak
*.orig
*~

# OS / エディタ
Thumbs.db
*.swp
.vscode/
.idea/

# 機密情報
.env
.env.*
*.pem
*.key
secrets/
```

> AI/ML 開発では **モデルファイル（`.gguf`, `.safetensors`, `.bin` 等）の除外が特に重要**。
> 数 GB のファイルを誤って読み込まないようにすること。

---

## チェックリスト（新規ツール作成時）

- [ ] Python 仮想環境（`uv` or `venv`）をセットアップ
- [ ] `.claudeignore` をプロジェクトルートに配置（モデルファイルの除外を確認）
- [ ] GPU 使用の有無を明確にし、不要なら CPU 処理に留める
- [ ] CUDA バージョンと PyTorch のビルドが一致しているか確認
- [ ] 依存ライブラリは最小限に絞る
- [ ] CI/CD・パッケージング不要であることを確認
