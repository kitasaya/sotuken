# タスク R1-prep：ground_truth.csv 再構築のための下準備自動化

日本の交通法規に準拠した自転車ナビゲーションシステム。
卒業研究（青山学院大学・宮治研究室）。リポジトリ：`kitasaya/sotuken`（`bicycle-navi/backend/`）。

---

## 背景

ルーティングロジックの変更（F1修正・進行方向照合の追加等）により、既存の
`ground_truth.csv`（渋谷→新宿6行のみ記入済み）は前提が古くなった。
15 O-Dペア全件を対象に、ground_truth.csv を1から作り直す。

このタスクは**下準備の自動化のみ**を対象とする。`true_oneway_violation` /
`true_two_step_required` の最終判定はマサヤさんが人手で行うため、Claude Code は
判定ロジックを実装しない。

---

## 不変の制約（変更禁止）

- `backend/data/ground_truth.csv` の `true_oneway_violation` / `true_two_step_required`
  列を埋めるロジックは実装しない。空欄のまま出力すること。
- `law_checker.py` の判定ロジックを模倣・再実装しない（循環論法になるため）。
- 既存の `check_sidewalk_violation` 不使用方針、v1/v3切替構造、右折危険性スコープ外
  方針は維持する。
- 生成するスクリプトは `backend/scripts/` 配下に新規作成し、既存ファイルは変更しない。

---

## タスク一覧

### タスク1：way_id 一括取得スクリプト

#### 目的

`od_pairs.csv` の15 O-Dペア全件について `/api/route` を叩き、
`label, way_id, point_lat, point_lng` を抽出したCSVテンプレートを自動生成する。

#### 新規ファイル

`backend/scripts/prepare_ground_truth.py`

#### 仕様

1. `backend/data/od_pairs.csv` を読み込み、全ラベルをループ処理する
2. 各ラベルについて `POST /api/route` を呼び出す（origin/dest座標は od_pairs.csv から取得）
3. レスポンスの `violations[]` から以下を抽出する：
   - `way_id`（`comparison.using_edge_ids: false` の場合は空欄とし、
     `notes` 列に `"fallback: way_id unavailable"` と記録する）
   - `point_lat`, `point_lng`
   - `rule`（oneway / two_step_turn）→ 参考列 `detected_rule` として出力（ground_truth判定には使わない）
   - `confidence` → 参考列 `system_confidence` として出力
4. 検出されたviolationsがゼロのラベルについても1行出力する（`way_id`空欄、
   `notes`に`"no violations detected by current system"`）。TN（真陰性）候補として
   人手で別途way_idを補完する余地を残すため。
5. 出力先：`backend/data/ground_truth_template.csv`（**既存の ground_truth.csv は上書きしない**）
6. 出力列：`label, way_id, point_lat, point_lng, true_oneway_violation, true_two_step_required, detected_rule, system_confidence, osm_tags_raw, notes`
   - `true_oneway_violation` / `true_two_step_required` は空欄で出力
   - `osm_tags_raw` はタスク2で埋める（タスク1時点では空欄）

#### 完了条件

- `python backend/scripts/prepare_ground_truth.py` を実行すると
  `ground_truth_template.csv` が生成される
- 15ラベル全件について最低1行（violations 0件の場合も含む）が出力される
- 既存の `ground_truth.csv` が変更されていないこと

---

### タスク2：OSM生タグの事前取得

#### 目的

タスク1で取得した `way_id` について、OSMの生タグを取得し `osm_tags_raw` 列に
埋め込む。マサヤさんがOSMサイトを毎回開かずに判定できるようにするため。

#### 変更対象

`backend/scripts/prepare_ground_truth.py` に追記（同一スクリプト内で完結させる）

#### 仕様

1. `services/overpass.py` の `get_way_tags_by_ids(way_ids: list[int])` を再利用する
   （新規実装しない。既存関数をインポートして呼び出すのみ）
2. タスク1で得た `way_id` リスト（空欄以外）を一括で `get_way_tags_by_ids` に渡す
3. 戻り値の `tags` 辞書から、判定に関連するキーのみを抽出して文字列化し
   `osm_tags_raw` 列に格納する：
   - `oneway`, `oneway:bicycle`, `cycleway`, `cycleway:left`, `cycleway:right`,
     `highway`, `junction`
   - 例：`"oneway=yes; cycleway=opposite_lane; highway=unclassified"`
4. 該当タグが1つもない場合は `"(no relevant tags)"` と記録する
5. Overpass取得失敗時は `osm_tags_raw` に `"(overpass fetch failed)"` と記録し、
   処理を継続する（既存の overpass.py のフォールバック仕様に準拠）

#### 完了条件

- `ground_truth_template.csv` の `osm_tags_raw` 列に、各行のwayの関連タグが
  文字列として埋まっている
- Overpass失敗時にスクリプトが停止せず、該当行のみエラー記録で継続する

---

### タスク3（参考・実装不要）：人手判定フロー

以下はマサヤさんが行う作業。Claude Code は実装しない。

1. `ground_truth_template.csv` の `osm_tags_raw` を見ながら、
   `true_oneway_violation` / `true_two_step_required` を人手で埋める
   （必要に応じて実際のOSMページも確認する）
2. 判定完了後、ファイルを `backend/data/ground_truth.csv` にリネーム／上書きする
3. `POST /api/experiment/ground-truth/compare` を実行し、Precision/Recall/F1を確認する

---

## 実行方法メモ

```bash
cd backend
python scripts/prepare_ground_truth.py
```

出力：`backend/data/ground_truth_template.csv`

---

## 完了時の記録

タスク完了時は `docs/CHANGELOG.md` に以下を追記する：
- 実装したスクリプトの概要
- 15ラベル中、way_id取得成功件数／フォールバック件数
- 生成された `ground_truth_template.csv` の行数

---

## 参照ドキュメント

- `docs/ARCHITECTURE.md`：`services/overpass.py` の `get_way_tags_by_ids` 仕様
- `docs/CHANGELOG.md`：R1タスクの過去の経緯（2026-05-18）
- `backend/data/od_pairs.csv`：対象O-Dペア一覧
