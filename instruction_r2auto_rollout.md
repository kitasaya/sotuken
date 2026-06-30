# R2-auto：残り O-D ペアへの展開と google_comparison.csv 流し込みの指示

採点器の実地検証は完了した（渋谷→新宿1本で oneway 逆走1件を検出、目視・OSMタグ・
システム判定の三者一致を確認済み）。次フェーズは **残り O-D ペアへの展開** と
**`google_comparison.csv` の自動流し込み**。更新済みの `CLAUDE.md` を必ず先に読むこと。

---

## このフェーズのゴール

15 O-D ペアそれぞれについて、Google ルートの違反エッジ数を採点器で算出し、
`google_comparison.csv` の `google_oneway_violation_count` /
`google_two_step_violation_count` /（あれば `google_total_violation_count`）を埋める。
最終的に「Google ルートには総計 N 件の違反エッジが観測された／本システムは同一 O-D で
0 件・距離増加 +0.0m」という R2 の中核対比が数値で揃う状態にする。

---

## 作業分担（重要）

- **Masaya**：各 O-D ペアの Google 自転車ルートを手動トレースして polyline（または
  `[[lng,lat],...]` 座標列）を作成し、入力ファイルに格納する。
- **Claude Code**：その入力を受け取り、採点器に通して結果を `google_comparison.csv` へ
  流し込むバッチ処理を実装・実行する。**経路探索・採点ロジック本体には手を加えない**
  （`external_route_scorer.py` の判定は検証済みのまま固定）。

### Masaya の polyline 作成は一括ではなく段階投入

15本を先にまとめて作る必要はない。下記ステップ1でまず **1〜2本** を使ってバッチ処理の
形を固め、Masaya が出力フォーマットを確認してから残りを流す。やり直しを防ぐため、
入力フォーマットを先に確定させることを優先する。

---

## 入力フォーマットの確定（ステップ0）

Masaya が polyline を貼り込む入力ファイルを1つ用意する。形式は採点器の入力に素直に
対応させる。候補（どちらでもよいが、Claude Code が実装しやすい方を選び、選定理由を
1行添えて Masaya に提示すること）：

- 案A：`backend/data/google_routes_input.csv`
  列：`label`（例 "渋谷→新宿"）, `origin`, `destination`, `polyline`（encoded string）
- 案B：`backend/data/google_routes_input.json`
  `[{ "label": "...", "polyline": "..." }, ...]`（座標列で渡す場合は `coords` キー）

`google_comparison.csv` の既存行と突き合わせるためのキー（`label` または O-D の
組）を必ず持たせること。既存 CSV の列名・キーを先に `view` で確認してから決める。

---

## ステップ1：バッチ採点スクリプトの実装（1〜2本で動作確認）

- 新規 `backend/scripts/score_google_routes.py`（命名は既存の
  `validate_scorer_groundtruth.py` に倣う）。
- 処理：入力ファイルを1行ずつ読み、`POST /experiment/external-route/score`
  （または `score_external_route` を直接 import）で採点し、結果を集約。
- 出力：各ペアの `oneway_violation_count` / `two_step_violation_count` /
  `total_violation_count` / `route_distance_m` / `sampled_points` を一覧化。
- まず Masaya が用意する **渋谷→新宿（検証済みの1本）+ もう1本** だけで実行し、
  渋谷→新宿が前回と同じく oneway=1 を返すか（リグレッション確認）を必ずチェック。
- この時点では `google_comparison.csv` へはまだ書き込まず、結果を標準出力か
  別の一時 CSV に出して Masaya に提示する。フォーマットの確認を取る。

## ステップ2：google_comparison.csv への流し込み

- ステップ1で Masaya の確認が取れたら、採点結果を `google_comparison.csv` の
  対応行へ転記する処理を追加する。
- **既存の手入力列（距離・時間など Masaya が別途記入する列）を上書き・破壊しないこと。**
  違反数の列のみを更新する。書き込み前に既存 CSV をバックアップ（コピー）してから行う。
- `label` 等のキーで突き合わせ、対応行が無い場合はスキップして警告を出す（新規行を
  勝手に作らない）。

## ステップ3：残り全ペアの実行

- Masaya が残りの polyline を入力ファイルに追加し終えたら、ステップ1〜2 を全件で実行。
- 完了後、`google_comparison.csv` の違反数列が全ペア埋まった状態を確認。

---

## 集計・確認（ステップ4）

- 全ペアの集計を出す：Google ルートの違反エッジ **総計**、ペアあたり平均、
  違反が観測されたペア数 / 15。
- 同じ O-D に対する本システムの違反数（0 のはず）と並べた対比表を作る。
- これを論文の R2 結果表の素データとする。CSV で出力。

---

## やってはいけないこと

- `external_route_scorer.py` の判定ロジック（向き整合チェック60度・折れ角45度・
  サンプリング40m）を **変更しない**。検証済みの状態で固定。数字を合わせにいく調整は禁止。
- 経路探索（`rerouter.py`）への干渉禁止（ゼロコスト主張に影響するため）。
- `google_comparison.csv` の Masaya 手入力列の上書き・行の自動追加・削除をしない。
- two_step の検出漏れ（緩いカーブ）は既知の限界。閾値を下げて取り繕わない。

---

## 完了条件

- `backend/scripts/score_google_routes.py` が実装され、入力ファイルから全 O-D ペアを
  採点できる。
- 渋谷→新宿でリグレッションが無い（oneway=1 を再現）。
- `google_comparison.csv` の違反数列が全ペア分埋まり、手入力列は無傷。
- Google 違反総計と本システム（0件）の対比表 CSV が出力される。
- `docs/CHANGELOG.md` に追記。

---

## 補足：論文での「Google ルート」の定義（実装不要・記録のため）

`google_comparison.csv` を埋めるにあたり、論文には「Google Maps 自転車モードが提示した
経路を手動トレースして polyline 化し、本システムと同一の判定器（向き整合チェック付き
座標ベース判定）で違反エッジ数を採点した」と明記する。トレース誤差および外部ルートが
osm_way_id を持たないことによる座標ベース推定の精度限界は、ステップ1（ground_truth）の
Precision/Recall として既に開示済み。主張は一貫して「Google ルートに違反エッジが N 件
観測された（観測事実）」であり、「Google が法規を無視している（意図）」ではない。
