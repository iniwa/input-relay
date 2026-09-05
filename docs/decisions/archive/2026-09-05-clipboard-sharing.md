# Main PC / Sub PC テキストクリップボード共有 設計

- 作成日: 2026-09-05
- 状態: 実装・独立レビュー・Main/Sub 配備・実機確認完了。
- 調査基準: input-relay `d330705`。開始時の作業ツリーに差分なし。
- 今回の分類: `non-implementation`。実装担当予定はユーザー指定の Sol。
- 引き継ぎ: [実装 handoff](../../handoffs/archive/2026-09-05-clipboard-sharing.md)

## 1. 要件と利用体験

ユーザー確認済みの範囲は、**テキストのみを双方向共有し、Main PC の
Shift + Scroll Lock で ON/OFF、Main PC に数秒の通知を表示する**こと。
Main PC は sender、Sub PC は receiver とする。

追加の細部は本設計で次のように定める。

| 項目 | 仕様 |
|---|---|
| 共有対象 | Windows の `CF_UNICODETEXT` として取得できるプレーンテキスト |
| 方向 | Main → Sub、Sub → Main の両方 |
| 操作 | 通常のコピー/切り取り後、相手 PC で通常どおり貼り付ける。Ctrl+C / Ctrl+V 自体は捕捉・注入しない |
| 対象期間 | ON 成立後にコピーされた内容のみ。ON 前の内容を初回送信・上書きしない |
| 初期値 | 起動時 OFF。ON/OFF を設定ファイルに保存しない |
| 切断・再接続 | OFF に戻る。再接続しても自動で ON にしない |
| リモート操作との関係 | 独立。リモート操作 OFF でも共有でき、共有切り替えで入力抑止を変更しない |
| 通知 | Main のメインディスプレイ右上に3秒。最新通知で置き換えてタイマーを再設定 |
| 通知の操作性 | 確認ボタンなし、非アクティブの小さな通知ウィンドウ。フォーカス・クリック・キーボード操作を奪わない |
| サイズ | UTF-8 換算で最大 65,536 bytes（64 KiB）。超過時は送信せず、切り詰めない |
| 非対象 | 画像、ファイル、HTML/RTF の書式、履歴、既存内容の一括同期、Sub 側ホットキー、standalone |

「通知モーダル」は作業を止める modal dialog ではなく、上記の自動消去する
通知として扱う。表示文字列は「クリップボード共有 ON」「クリップボード共有 OFF」。
ON 成立前は成功表示しない。接続不能時は「共有できません：Sub PC 未接続」、
相手未対応時は「共有できません：相手側の更新が必要です」、共有中の切断時は
「クリップボード共有 OFF：接続が切れました」とする。

ON 中に共有先のクリップボードは置き換わる。OFF にしても、既に相手へ渡った
テキストや OS の履歴は消去・復元しない。内容からパスワード等を判別する機能は
設けないため、ON 中にコピーするテキスト全体が対象になる。

## 2. 現行実装で確認した接続点

| ファイル / 関数 | 現状と設計上の扱い |
|---|---|
| `sender/input_sender.py:on_press` | Scroll Lock の押下で即座に `_set_remote_mode` を呼ぶ。Shift 分岐と押下ラッチを先に追加する |
| `sender/input_sender.py:on_release` | 現状は Scroll Lock を含め release を通常送信する。消費した Scroll Lock の down/up を一対で除外する |
| `input_common/input_events.py` | 左右 Shift を表示名 `shift` に統合する。ホットキー判定にはこの表示名 set を流用しない |
| `sender/input_sender.py:sender` | 入力 WS 接続直後の明示的 `remote_control` 送信、子 task 回収、切断時 OFF を維持する |
| `sender/input_sender.py:_emit` | receiver と monitor の両方に送る。クリップボード本文には使用禁止 |
| `receiver/input_server.py:sender_handler` | 通常入力以外の未知メッセージも browser へ転送する。本文をここへ流さないことが必須 |
| `receiver/input_server.py:ws_handler` | 現状 `/browser` 以外を sender として扱う。新しい `/clipboard` を明示的に先行分岐する |
| `sender/overlay_window.py:OverlayManager` | リモート用 blocker と `focus_force` / `SetForegroundWindow` を持つ。共有通知に `show()` を流用しない |
| `sender/http_api.py`, `sender/sender_gui.html` | `/api/status` と既存 polling がある。本文を含めず状態だけを追加できる |
| `start_receiver.bat` | receiver の依存は `websockets` のみ。共有のために pynput / pygame / Qt 等を追加しない |

現行契約は [docs/api.md](../api.md) を正とする。本設計の新規契約をそちらへ
転記するのは実装と同時。設定形を変更しないため `*.example.json` の変更も不要。

## 3. 構成

```text
Main PC / sender                         Sub PC / receiver
  既存入力キャプチャ ── 既存 WS / ─────── 入力表示・リモート注入
        │ capability offer を受け取る         │
  共有制御・専用 WS client ═ WS /clipboard ═ 共有制御・専用 handler
        │                 同じ既存ポート       │
  Win32 clipboard worker                 Win32 clipboard worker
        │                                    │
  ローカルクリップボード                    ローカルクリップボード
        │
  非アクティブ通知（Main のみ）
```

`/clipboard` は既存 receiver WebSocket サーバーの同じ port（既定 8888）に
追加する。本文用の TCP 接続を分け、大きな JSON や相手の送信詰まりを既存入力
WS の送信順待ちに持ち込まない。新しい listen port、firewall、認証、TLS、
起動手順、外部サービスは追加しない。CPU と LAN 自体は共有されるため、完全な
遅延分離を保証するものではない。

既存の信頼済み private LAN / 無認証という境界を継承する。後述の `session`
は接続の取り違え防止用であり、認証や暗号化の代替ではない。Deskflow と同等の
セキュリティ特性を持つという説明はしない。

### 実装モジュール

| 新規ファイル案 | 責務 |
|---|---|
| `input_common/clipboard_win32.py` | ctypes 宣言、非表示 HWND、変更検知、テキスト read/write、自己更新判定、停止 |
| `input_common/clipboard_sync.py` | v1 メッセージ検証、世代・revision、状態機械、有界 mailbox。OS/API を注入可能にする |
| `sender/clipboard_client.py` | capability 受信後の専用接続、Main の共有要求、worker 接続、状態 snapshot |
| `receiver/clipboard_server.py` | 入力接続との関連付け、専用 handler、Sub ローカル変更の受付、更新順の確定 |
| `sender/notification_window.py` | Win32 の非アクティブ通知。固定1スレッド・1ウィンドウ・最新通知1件 |

これ以上の汎用プラグイン機構・イベント基盤は作らない。入力共通化や既存
OverlayManager のリファクタリングは本機能の前提にしない。

## 4. 接続と状態

### 能力確認と互換性

1. 新 receiver は現在の入力 sender 接続ごとにランダムな `session`（UUID）を
   発行し、既存 `mode_switch` に続いて `clipboard_offer` を送る。
2. 新 sender は v1 offer を受けた場合だけ、同じ host/port の `/clipboard` を
   開いて `clipboard_bind` を送る。旧 receiver へ新しいパスを試行しない。
   現状の「未知のパスは入力 sender」処理で入力接続が置き換わるのを防ぐ。
3. receiver は現在の入力 WS に属する session のみ受理する。専用接続は1本まで。
   重複 bind は新しい方を拒否し、既存接続を黙って置き換えない。
4. Main は worker/通知の準備成功後に bind を送り、Sub は自分の worker の準備を
   確認して OFF の `clipboard_state` を返す。
   bind 待ちは3秒で打ち切り、本文を処理せず閉じる。
5. 入力接続の切断・置換時は、その接続に属する共有世代を無効化して専用接続を
   閉じる。古い handler の finally で新しい session を OFF にしない。

旧 sender は offer を無視して従来動作を継続する。新 sender + 旧 receiver は
共有 unavailable のまま従来の入力表示・リモート操作を継続する。
新 receiver の通常 sender 経路では `clipboard_*` を誤送信されても消費/拒否し、
browser や入力注入へ流さない。standalone は offer を出さず `/clipboard` を拒否する。

### 状態遷移

| 現状態 | 操作・事象 | 次状態 / 動作 |
|---|---|---|
| unavailable | 専用接続と worker 準備完了 | off |
| unavailable | ホットキー | OFF のまま理由を3秒通知。ON 要求を予約しない |
| off | ホットキー | enabling。Main の現 sequence を基準化し、ON 要求を送る |
| enabling | receiver が新 epoch を準備して ON state を返す | Main も基準化して on。ON を3秒通知 |
| enabling | 再押下 | disabling。ON を取り消して OFF 要求。遅れて来る ON 応答を無視 |
| on | ホットキー | Main は直ちに読出し/送信/適用を無効化。disabling として OFF 要求 |
| disabling | receiver の OFF 応答 | off。OFF を3秒通知 |
| disabling | 再押下 | 処理中として無視。OFF 完了後の新たな押下で ON にできる |
| 任意 | 専用接続切断・状態応答3秒超過・worker 致命エラー | unavailable。共有 OFF、本文待機枠を破棄 |
| 任意 | 入力接続切断・置換・終了 | 共有 OFF、epoch 無効化、関連 task/worker 後始末 |

通常の OFF は入力 WS を切断しない。専用接続だけが故障した場合もリモート操作
や入力抑止には手を触れない。専用接続の再試行は固定1 task で既存の backoff
方針を利用し、再試行に成功しても off。入力接続終了時には必ず cancel/await する。

ON は両端で基準 sequence を取得してから、その後の変更を対象とする。ON 完了
通知より前の準備時間中のコピーは共有保証外とし、古い内容の追送を行わない。
OFF は Sub が要求を受信して適用を止めた時点で両端に成立する。通信遅延中や
OFF に先行して開始済みの1回の OS 書き込みは取り消せない。成功通知は応答後に
出し、タイムアウト時は「共有停止：接続を確認してください」と区別する。

## 5. v1 通信契約案

JSON text message を使う。整数フィールドは bool を受理せず、非負整数を厳密に
検証する。session/epoch は UUID 文字列、request/origin sequence/revision は
接続または epoch 内の単調増加整数。壁時計は順序決定に使わない。

| type / 経路 | 必須フィールド（type 以外） | 意味 |
|---|---|---|
| `clipboard_offer` / 入力 WS、Sub → Main | `version:1`, `session` | 本文を含まない能力通知。path はコードで `/clipboard` に固定 |
| `clipboard_bind` / 専用 WS、Main → Sub | `version:1`, `session` | 現在の入力接続との関連付け |
| `clipboard_set` / 専用 WS、Main → Sub | `request_id`, `enabled` (bool) | 明示的な状態要求。toggle 命令にはしない |
| `clipboard_state` / 専用 WS、Sub → Main | `request_id`, `enabled`, `epoch`, `reason` | bind 初回は request_id=0、enabled=false、epoch=null。OFF も epoch=null。ON ごとに新 epoch |
| `clipboard_propose` / 専用 WS、Main → Sub | `epoch`, `origin_seq`, `text` | Main のローカル変更提案 |
| `clipboard_update` / 専用 WS、Sub → Main | `epoch`, `revision`, `origin`, `origin_seq`, `text` | receiver が順序を確定した更新。origin は `main` または `sub` |
| `clipboard_error` / 専用 WS、双方向 | `epoch`, `code` | 共有処理の失敗。本文や例外 repr は送らない。ON 前は epoch=null |

例（epoch は説明用の UUID）:

```json
{"type":"clipboard_propose","epoch":"00000000-0000-4000-8000-000000000001","origin_seq":1,"text":"こんにちは\r\nMain PC からコピー"}
```

`request_id` の応答照合と epoch 検証により、取り消した ON や以前の ON 時代の
本文を無視する。bind 済み接続自身が session を保持し、以後すべてに session を
重複送信しない。未知 type、不正型、未知 epoch の本文は適用しない。
構造/サイズ違反は専用接続のみ終了して共有 OFF。`reason` / `code`
は `user`, `disconnected`, `unavailable`, `busy`, `too_large`, `invalid_data`,
`write_failed`, `worker_failed` の固定語彙から選ぶ。
`clipboard_error` の `too_large` はローカルで送信を見送った通知なので ON を維持し、
それ以外の処理失敗は両端を OFF にして専用接続を閉じる。意図的な OFF は
`clipboard_set/state` で扱う。非同期のエラーは request_id の一致を待たず、現在の
epoch と一致する場合に停止する。

### 同時コピーと反射防止

- receiver の共有制御 loop を順序の唯一の決定者にする。Main の propose と Sub
  worker のローカル変更を同じ受付へ渡し、受付順に revision を増加させる。
  origin ごとに最大 origin_seq だけを保持し、それ以下の重複提案を再確定しない。
  同様に最大適用済み revision 以下の update は再適用しない。
- 確定更新は Sub の適用 mailbox と Main の専用送信 mailbox の両方へ渡す。
  発信元 Main にも update を返す。静止後には両端が最大 revision の内容になる。
- 同時コピーは「receiver が最後に受理した変更が勝つ」。実時間で最後のコピーを
  保証しない。通信前に連続コピーした中間内容は最新1件へまとめてよい。
- 適用 worker は同一 epoch の小さい revision を捨てる。ローカルコピーの観測と
  remote 適用を直列化し、未観測のローカル変更は上書き前に提案として渡す。
  提案済み snapshot は、それより古い確定更新で OS 内容が一時上書きされても
  保持し、受付後の新 revision で両端を収束させる。
- 発信元側で現在 sequence が当該ローカル snapshot と一致する場合は、確定通知
  で OS を書き直さない（元アプリが提供する書式を不必要に失わないため）。
- remote write の直後、clipboard を閉じる前に自己書き込みの sequence を記録。
  その sequence の変更通知だけを無視する。通知を1回だけ無視するフラグや、
  「同じ文字列を永続的に無視する」方式にはしない。別のコピーなら同文も送れる。
- sequence は Windows の変更検知用であり分散 revision ではない。0 は取得不能、
  wraparound は値の大小でなく変化として扱う。履歴 set や無期限 hash cache を作らない。

### 有界化と本文の隔離

- 各端のローカル読出し要求・本文送信・OS 適用はそれぞれ最新1件の mailbox。
  実行中1件と待機中1件以上に増やさない。通知も最新1件。
- 制御要求/応答は本文と別枠にし、OFF/終了を優先。thread → asyncio の橋渡しも
  通知 callback を1件に集約し、`call_soon_threadsafe` をイベントごとに蓄積しない。
- 本文送信は各端最大10回/秒にまとめる。送信・状態応答には3秒の期限を設け、
  timeout で専用接続を閉じる。途中の send を cancel して同じ接続で再利用しない。
- UTF-8 本文は64 KiB以下。JSON message 自体は400 KiB以下を parse 前に検査する。
  JSON escape の膨張も考慮した値で、receiver 全体の既存 WS 上限は増やさない。
- 内容を通常 event_queue、monitor、`/browser`、HTTP response、OBS HTML、
  ログ、設定、ディスク、履歴へ出さない。本文を含む例外オブジェクトのログも禁止。
  OFF/切断時は参照を解放するが Python メモリの完全消去は保証しない。

## 6. Windows クリップボード処理

標準ライブラリ `ctypes` と user32/kernel32 を使用する。各プロセスに固定1本の
専用 worker と非表示 HWND/message loop を設ける。import 時にウィンドウや
クリップボードへ触れず、2PC モードの共有初期化時に明示的に start する。

`AddClipboardFormatListener` / `WM_CLIPBOARDUPDATE` で変更を検知する。
OFF 中は通知を捨て、本文を読まない。ON 成立時は sequence だけを基準化する。
HWND callback では dirty 状態を更新するだけにし、read/write・再試行は worker
上の後続処理へ渡す。入力 callback、asyncio の入力/注入経路、Tk thread では
クリップボード API を呼ばない。

読出しは `OpenClipboard(hwnd)` → `GetClipboardData(CF_UNICODETEXT)` →
`GlobalSize` / `GlobalLock` → 範囲内コピー → unlock → close。
UTF-16LE の終端 NUL、偶数長、Unicode とサイズを検査し、無制限の `wstring_at`
は使わない。Win32 buffer は最大128 KiB + 終端2 bytesまで読み、UTF-8 変換後にも
64 KiBを検査する。利用者の改行、空白、タブ、絵文字を勝手に正規化しない。
wire の埋め込み NUL・孤立 surrogate は拒否する。

テキスト形式がない画像/ファイルや空の clipboard は共有しない。空文字の
`CF_UNICODETEXT` も無視する（相手を空にする操作は初版の対象外）。HTML 等と
テキストが同時にある場合はテキストだけを送り、相手では書式なしになる。

書込みは有効な HWND を owner とし、UTF-16LE + 終端 NUL の `GMEM_MOVEABLE`
メモリを先に準備する。Open 成功後に epoch/enabled と未観測ローカル更新を確認し、
`EmptyClipboard` → `SetClipboardData(CF_UNICODETEXT, handle)` を行う。
成功した handle は OS 所有へ移り、失敗した未移譲 handle だけを解放する。
Open 成功後の Close と GlobalLock 成功後の Unlock は finally で保証する。
ctypes の pointer/handle と callback は64-bitに対応する型宣言・寿命保持を行う。

Open 競合は初回 + 20/50/100/200 ms の最大4回再試行とし、最新要求への置換と
OFF を各試行前に確認する。枯渇時は `busy` として共有 OFF にして両端へ通知する。
サイズ超過/非対応ローカル内容は送らず、共有は ON のままにする。超過は Main へ
内容を含まない通知を出し、同種通知は5秒につき1回にまとめる。

`EmptyClipboard` 後の Set 失敗は元内容を完全復元できないため、黙って成功せず
`write_failed` で共有 OFF とする。全形式のバックアップや推測による復元は行わない。
他アプリの遅延レンダリングによる Win32 呼出し時間はアプリ側の retry だけでは
上限保証できない。worker 監視3秒超過では共有を無効化し、遅れて返った本文を
epoch で破棄する。旧 worker が生存中に代替 worker を増殖させない。

終了時は先に共有を無効化し、本文待機枠を破棄して専用 task を cancel/await。
worker に停止を通知し、`RemoveClipboardFormatListener`、HWND/クラス解放、
thread join を行う。join 待ちは2秒まで。OS 呼出しが戻らない場合は利用不可のまま
停止待ちを打ち切り、プロセス終了を妨げない thread とする。通常停止で resource が
残らないことと、異常時に入力常駐機能を止めないことを分けて検証する。

## 7. ホットキーと通知

Main の既存 pynput keyboard listener に分岐を加える。別の常駐 keyboard hook
や Sub の pynput 依存は追加しない。

1. 左右 Shift の物理 VK を独立に追跡する。両方押して片方を離しても Shift 有効。
   listener 交換時の重複 callback は同じロックと Scroll Lock ラッチで除重する。
2. Scroll Lock 最初の down で Shift の有無を確定する。Shift ありなら共有要求、
   なしなら既存リモート操作。Scroll Lock を離すまで autorepeat は両方とも無視。
   押下中に Shift を変えても別機能へ切り替えない。
3. Scroll Lock の release でラッチ解除し、消費した down/up は receiver/monitor
   に送らない。Shift 自身の down/up は通常経路で対を保ち、後から片側を消さない。
4. listener 再起動でラッチを無条件解除しない。起動時に既に押されている Shift、
   取りこぼした release からの復帰は Win32 の物理状態確認で補う。
5. 共有分岐は要求を enqueue して戻るだけ。`_set_remote_mode`、listener 再起動、
   cursor lock、入力注入、overlay blocker は呼ばない。

この分岐は既存 listener が届ける押下を扱う。Scroll Lock の OS LED/ローカル
アプリへの抑止を新たな機能として実装しない。物理入力と既存のリモート抑止下の
両方で押下判定を実機確認する。

通知は既存リモートウィンドウから独立した小さい Win32 popup とする。
`WS_EX_NOACTIVATE` / `WS_EX_TOOLWINDOW`、非アクティブ表示と
`SetWindowPos(... SWP_NOACTIVATE ...)` を使い、生成から表示まで activation を
起こさない。クリック透過も実装し、`WS_EX_TRANSPARENT` だけで十分と仮定しない。
`focus_force`、`SetForegroundWindow`、grab、全画面 blocker は使わない。
ディスプレイ作業領域と DPI を考慮して右上へ置く。3秒は単調時計/OS timerで計測。
排他フルスクリーン上の可視性は保証外とし、通常デスクトップとボーダーレスを確認する。

通知初期化に失敗した場合は ON を成立させず unavailable にする。OFF は表示障害に
関係なく実行できる。`remote_overlay.enabled=false` や Pause の表示非表示設定は
この通知には影響させない。

## 8. 状態表示と設定

Sender `GET /api/status` に以下を追加し、GUI の既存 polling で表示する。
クリップボード用 POST API や GUI トグルは初版では追加しない。

```json
{"clipboard":{"state":"off","enabled":false,"reason":"user"}}
```

state は `unavailable|off|enabling|on|disabling`、enabled は `state == "on"`。
reason は前述の固定語彙。本文、session、epoch、接続識別子は出さない。
GUI 表示は「クリップボード共有：OFF（Shift + Scroll Lock で切替）」等とする。
通知が消えた後も状態を確認できる。receiver 側 GUI/API には追加しない。

ON/OFF・ホットキー・サイズ上限・通知秒数は初版では固定とし、新しい永続設定を
増やさない。Main/Sub 各 PC のローカル設定所有境界を変更しない。

## 9. 受け入れと検証

安全性に直接関係する新規動作の focused test を作る。mock Win32、fake WS、
一時設定ディレクトリを用い、unit test が実 clipboard や hook を起動しないようにする。

| 必須確認 | 合格条件 |
|---|---|
| 双方向通常操作 | 日英、複数行、タブ、絵文字が一致し、リモート操作 OFF/ON の両方で貼付可能 |
| 開始/終了境界 | 初期 OFF、既存内容を ON 時に送らない、OFF 後の新規コピーを送らない、再接続後 OFF |
| 同時更新 | Main/Sub 同時提案が最大 revision に収束。自己書込み反射なし、同文再コピーは妨げない |
| 世代 | OFF→ON、遅延応答、旧 socket cleanup、古い write 待機枠が新 session を変更しない |
| キー | 左右 Shift、両 Shift、長押し、押下順変更、listener 交換で1押下1切替。Shift 併用で RC 不変 |
| 既存安全性 | RC disconnect OFF、実 VK の解放、マウス抑止、Pause、既存 mode_switch が維持される |
| 本文隔離 | monitor/browser/HTTP/ログにテスト本文が出ず、不正経路への clipboard_* も転送しない |
| 上限・OS失敗 | 境界64 KiB、超過、空/非テキスト、Open 競合、Set 失敗で仕様どおり。resource/待機枠が増えない |
| 互換性 | 新旧の組合せで入力機能継続。offer なしで `/clipboard` を開かず、standalone で worker 起動なし |
| 通知 | 約3秒、連打で最新1枚、フォーカスと背後のクリック/キー操作を妨げず RC blocker を変更しない |

実装時の静的/自動確認:

- 変更 Python を列挙して `python -m py_compile <files>`。
- `python -m unittest discover -s tests -p "test_clipboard*.py"`、ホットキー/通知の
  focused test、および既存 `test_remote_control.py` / `test_sender_gui_static.py`。
- 複数プロセスの経路を変更するため、一度 `python -m unittest discover -s tests`。
- established 環境にあれば `python -m ruff check .`。依存は追加しない。
- `git diff --check` と差分自己レビュー。

実機では Main sender と Sub receiver の2台、無害なダミーテキストだけを使う。
既存クリップボードを読んで報告したり、試験のために私的内容をログへ残したりしない。
通常コピー → 相手貼付、逆方向、OFF、通知中の継続入力、RC 併用、接続断を確認する。
大規模な耐久試験は初版の必須にしないが、連続コピー/切替で待機枠と worker が
増えないことは確認する。実機条件がない場合は source-ready と runtime-verified を
区別し、この handoff に未完項目・再開条件を残す。

安定した実装と自己レビューの後、runtime 適用前に本文漏出、旧接続の失効、
入力抑止の回帰、Win32 の所有権と非 activation を重点レビューする。
今回は設計のみであり、live clipboard、hook、socket、常駐プロセスは操作していない。

## 10. Deskflow と一次資料からの参照

調査日: 2026-09-05。Deskflow のリンクは `master` のため将来変化する。
以下は実装方針の参考であり、この設計の protocol/制限値は input-relay 向けの判断。

- [Deskflow MSWindowsScreen.cpp](https://github.com/deskflow/deskflow/blob/master/src/lib/platform/MSWindowsScreen.cpp):
  listener 登録/解除と `WM_CLIPBOARDUPDATE` の sequence 比較を参考にする。
- [Deskflow MSWindowsClipboard.cpp](https://github.com/deskflow/deskflow/blob/master/src/lib/platform/MSWindowsClipboard.cpp):
  OS 操作と形式変換の分離、Open 競合の有限 retry、自己所有判定を参考にする。
  初版では独自 clipboard ownership format を作らず、自己 write sequence で抑制する。
- [Deskflow README](https://github.com/deskflow/deskflow):
  clipboard 共有と既定 TLS の説明がある。Qt/C++ の依存・独自通信・他OS対応は移植しない。
  ソースは GPL-2.0-only WITH OpenSSL exception の表示があるため、コードの複製・翻訳移植は
  本範囲に含めず、Microsoft の公開 API を用いて独立実装する。
- [Microsoft: AddClipboardFormatListener](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-addclipboardformatlistener)
  と [Using the Clipboard](https://learn.microsoft.com/en-us/windows/win32/dataxchg/using-the-clipboard):
  HWND での変更通知と message loop の根拠。
- [Microsoft: GetClipboardSequenceNumber](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclipboardsequencenumber):
  変更検知と取得不能時の0の扱い。
- [Microsoft: GetClipboardData](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclipboarddata)
  / [SetClipboardData](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setclipboarddata):
  read handle の寿命、write handle の所有権移譲、有効 HWND の使用。
- [Microsoft: Standard Clipboard Formats](https://learn.microsoft.com/en-us/windows/win32/dataxchg/standard-clipboard-formats):
  `CF_UNICODETEXT` の UTF-16 と終端。
- [Microsoft: Extended Window Styles](https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles)
  / [SetWindowPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos):
  非 activation の通知ウィンドウ設計。

## 11. 採用しない案

- 通常入力 WS へ本文を混載: 既存の browser 転送と送信順待ちに影響するため。
- GUI の JavaScript Clipboard API: GUI/OBS を開かない常駐運用と両立しないため。
- Ctrl+C / Ctrl+V の検知だけで送受信: メニューからのコピーやリモート操作以外のコピーを扱えないため。
- 初回全同期/ON 状態保存: 意図しない過去の内容の送信と上書きを避けるため。
- 画像/ファイル転送、Deskflow 本体の導入: 今回確認済みのテキスト共有を越えるため。
- 既存 remote overlay の通知への転用: フォーカス取得と blocker を共有してしまうため。
