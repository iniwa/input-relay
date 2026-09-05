# Sol 向け: テキストクリップボード共有の実装引き継ぎ

- 作成日: 2026-09-05
- 状態: source-ready / correction round 1 offline 検証済み。実機確認と Codex 最終再レビュー待ち。
- 担当予定: ユーザーが実装を依頼する Sol。今回は agent/task を起動していない。
- 設計: [Main PC / Sub PC テキスト共有](../decisions/2026-09-05-clipboard-sharing.md)
- 基準 commit: `d330705`。引き継ぎ開始時に差分を再確認すること。
- 次回実装の分類: `adaptive`（Win32 clipboard、非 activation、入力 listener と
  接続世代の横断動作を実機で成立させる必要がある）。分類は委譲を強制しない。

## 目的・背景

Main/Sub 間でテキストを双方向共有する。Main の Shift + Scroll Lock で切り替え、
Main に3秒の非アクティブ通知を表示する。この対象形式と操作PCはユーザー確認済み。
Scroll Lock 単体は既存リモート操作を維持する。設計書の初期 OFF、ON 後のコピー
だけを共有、専用 `/clipboard`、サイズ上限、状態機械を一体として実装する。

既存 receiver は未知メッセージを browser へ転送し、未知パスを入力 sender として
扱うため、能力確認前の新パス接続や入力経路への本文送信をしてはならない。

## 最初に読むもの

- `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/api.md`、上記設計書。
- `sender/input_sender.py`, `sender/http_api.py`, `sender/sender_gui.html`。
- `receiver/input_server.py`, `sender/monitor_ws.py`, `sender/overlay_window.py`。
- `input_common/input_events.py`, `receiver/input_injector.py`。
- `config/sender_config.example.json`, `config/config.example.json` と3つの起動 bat。
- `tests/test_remote_control.py`, `tests/test_sender_gui_static.py`,
  `tests/test_monitor_ws.py`, `tests/test_overlay_input_reset.py`。
- `docs/improvements.md` の reconnect task cleanup、disconnect reset、RC suppression、
  fail-closed、実 VK tracking の完了記録。

設計根拠と外部一次資料は設計書 §10。native 実装時には該当 API の契約を再確認する。
実機作業の判断前に private local-environment reference を読むが、その内容を転載しない。

## 編集範囲と作業単位

一人の writer がこの機能と直接の回帰確認を所有する。開始前から存在する差分は
他者の変更として保存し、無関係な整理を混ぜない。

- 新規: `input_common/clipboard_win32.py`, `input_common/clipboard_sync.py`,
  `sender/clipboard_client.py`, `receiver/clipboard_server.py`,
  `sender/notification_window.py`。
- 統合: `sender/input_sender.py`, `receiver/input_server.py`,
  `sender/http_api.py`, `sender/sender_gui.html`。
- 直接検証: 新規 `tests/test_clipboard*.py` とホットキー/通知用の focused test。
  既存 `tests/test_remote_control.py`, `tests/test_sender_gui_static.py` は必要部分のみ。
- 同期: `README.md`, `docs/api.md`、この handoff の実施記録。
  設計との差が必要なら実装前に根拠を記録して範囲を確認する。

最初の編集は Win32 を import/start しない `clipboard_sync.py` の protocol 検証と
状態機械から始められる。次に worker・専用経路・hotkey/通知を統合し、安定差分の
自己レビュー後にレビューと必要な実機確認を行う。同じファイルへ複数 writer を置かない。

## 制約・非対象

- 今回は文書のみ。次回の実装依頼があるまでソース編集・実機操作を開始しない。
- 新規 `/clipboard` と入力 WS の capability offer、Sender status の追加が、
  本設計で提案する契約変更の全体。実装依頼時にこの設計を採用した範囲で実施する。
- dependency、port、listen、認証、firewall、launcher、startup、persistent config、
  submodule、配布/デプロイ手順を変更しない。実設定は調査資料にしない。
- 本文は clipboard 専用処理と専用 WS だけで扱う。通常入力/monitor/OBS/HTTP/
  ログ/ディスクへ流さない。読み書きは専用 worker のみ。
- 共有切り替えでリモート操作状態・suppression・cursor lock を変更しない。
- no commit / push / secretary-bot pointer update / deployment。
- 実機確認では clipboard read/write、live hook/input、socket と resident process の
  操作が必要。次回依頼の live/integration 範囲に含まれる場合のみ、既知の2台で実施する。
  範囲外ならコードと offline 検証を完成させ、その実機確認だけを未完として記録する。

## 完了条件・検証・報告

設計書 §9 の必須表と検証コマンドを適用する。実機確認と区別して自動検証結果を
記録し、実 clipboard を触らない test fixture を使う。必要な fake の追加に留め、
汎用 offline harness や別の大規模改善を作らない。

writer の安定自己レビュー後、本文漏出、接続世代失効、入力抑止の回帰、Win32
所有権/非 activation の具体的リスクをレビューする。レビュー後に修正した場合の
扱い、2回目の correction round での契約リセットは `AGENTS.md` に従う。

報告には変更ファイル、実現動作、検証コマンドと結果、レビュー結果、実機の
確認済み/未確認、残りの差分と再開条件、subagent 使用有無を含める。
未完で終了する実装 run は `status=interrupted` とし、未完部分を明確にする。
実装・検証・レビュー・必要な実機確認がすべて完了してから archive へ移動する。

## 今回の記録

- 設計文書と本 handoff のみ作成。ソース・設定・launcher は未変更。
- Deskflow と Microsoft の一次資料、および既存ソースを read-only 調査。
- live clipboard、hook、入力注入、socket、常駐プロセス操作なし。
- 文書確認: `git diff --check`、新規文書の末尾空白検査、相対リンク存在確認を実施。
- 実装と実機検証は未着手。次の開始条件は、この設計に基づく Sol への実装依頼。

## 2026-09-05 実装記録

- `input_common/clipboard_sync.py` に v1 message の厳密検証、64 KiB/400 KiB 上限、
  receiver revision 確定、最新1件 mailbox を実装した。
- `input_common/clipboard_win32.py` に import/start 時に実 clipboard を触らない注入可能な
  Win32 backend と固定 worker を実装した。`CF_UNICODETEXT` の有限長 read/write、
  Open retry、sequence による自己反射抑止、OFF/epoch 検査、3秒 watchdog、2秒 join と
  生存中 worker の非増殖を含む。
- `receiver/clipboard_server.py` と `sender/clipboard_client.py` に入力接続 session と
  `/clipboard` bind、初期 OFF、明示 set/state、epoch/revision、切断 cleanup、最大10回/秒の
  本文送信を実装した。通常入力/monitor/browser/HTTP に本文を渡さない。
- `sender/input_sender.py` の既存 keyboard listener に左右 Shift の物理 VK と Scroll Lock
  latch を追加した。Shift + Scroll Lock は clipboard 要求を enqueue するだけで、Scroll Lock
  単体の Remote Control 経路と抑止順序は維持した。左右 Shift の通常 down/up も VK ごとの
  対を維持する。
- `sender/notification_window.py` に `WS_EX_NOACTIVATE` / `WS_EX_TOOLWINDOW` と
  `SWP_NOACTIVATE`、transparent hit-test を使う固定1 thread の約3秒通知を実装した。
- Sender `GET /api/status` と GUI、`README.md`、`docs/api.md` を同期した。永続設定、port、
  launcher、dependency、認証、firewall、外部公開は変更していない。
- 設計との差異: native clipboard worker の3秒監視は、clipboard API を呼ぶ専用 thread と
  固定 watchdog thread に分けた。watchdog 後も古い native thread が生存中なら代替 worker を
  作らず unavailable のままにする。その他の protocol・状態・上限・UI 契約に意図的な差異なし。
- offline test は fake Win32/backend/WebSocket とメモリ上の状態だけを使用した。live clipboard、
  hook、input injection、socket、browser、OBS、resident process、実設定は操作していない。
- offline 検証: `py -3.11 -m unittest discover -s tests -p "test_clipboard*.py"` 31件、
  focused `tests.test_remote_control` + `tests.test_sender_gui_static` 44件、全 suite 183件が成功。
  変更 Python の `py -3.11 -m py_compile ...`、`py -3.11 -m ruff check .`、
  `git diff --check` も成功した。
- 未確認の実機項目: Main/Sub 双方向の日本語・英語・複数行・tab・絵文字、Remote Control
  OFF/ON 併用、物理 Shift/Scroll Lock と listener 交換、通知の約3秒・非 activation・背後入力、
  clipboard Open 競合/Set 失敗、接続断、連続 copy 時の worker/mailbox 非増殖。
- 再開条件: 既知の Main/Sub 2台で無害なダミーテキストだけを使えるとき、設計 §9 の実機表を
  実施し、本文漏出・旧接続失効・入力抑止・Win32 ownership/non-activation の最終レビューを通す。

## 2026-09-05 配備記録（途中）

- 実装を `4c499de` (`Add bidirectional clipboard sharing`) として standalone repository の
  `origin/main` へ push した。
- Sub PC の既存 receiver を `DELETE /api/restart` で再起動し、新しい PID で HTTP/WS listen、
  Remote Control OFF、既存 Main sender の再接続を確認した。
- Main PC の実運用 sender は standalone checkout ではなく、`secretary-bot` の pinned submodule
  を含む release 配下から起動していることを確認した。Main の既存 sender は旧版のため、
  capability offer を無視して通常の入力転送だけを継続している。
- `secretary-bot` 親 checkout には本件開始前から config 2件、submodule pointer、runtime log 2件の
  未コミット差分がある。これらは本件の commit に含めず、変更・削除していない。
- Main への配備には、standalone の通常 origin とは別の mirror への push、`secretary-bot` の
  submodule pointer 更新、検証済み release の作成と切替が必要。これらは project rule 上の
  別承認対象なので未実施。
- 実機の双方向 clipboard、物理 hotkey、通知、接続断試験は Main の同一版配備後に実施する。

## 2026-09-05 correction round 1

- 独立レビュー指摘により、`CF_UNICODETEXT` は最初の UTF-16 NUL を論理終端として扱い、
  `GlobalSize` が返す割当領域の残りに非ゼロbyteがあっても拒否しないよう修正した。バッファ上限、
  偶数長、終端存在、UTF-16 decode と UTF-8 64 KiB 検証は維持した。非ゼロ trailing allocation を
  持つ ctypes fake fixture の直接回帰を追加した。
- receiver worker を入力sessionごとの一意な owner identity に束縛した。register/unregister/handler
  finally はlock内で対象workerをatomicにdetachし、lock外ではその参照だけを停止する。旧workerの
  callbackもowner一致時だけmailboxへ入る。遅延close中に新sessionがbind/startする決定的async回帰で、
  旧register cleanup、旧handler finally、旧unregisterが新workerを停止しないことを確認した。
- correction 後の offline 検証: clipboard focused 33件、Remote Control + Sender GUI focused 44件、
  全 suite 185件が成功。変更 Python の `py -3.11 -m py_compile ...`、
  `py -3.11 -m ruff check .`、`git diff --check` も成功した。
- correction 中も live clipboard、hook、socket、process、実設定は操作していない。commit、push、
  deploy は行っていない。実機未確認項目と再開条件、設計との差異は前節から変更なし。
