# 自転車ナビゲーションシステム 開発指示書

日本の交通法規に準拠した自転車ナビゲーションシステム。
卒業研究（青山学院大学・宮治研究室）のシステム実装プロジェクト。

---

## プロジェクト現状（2026-05-12）

STEP 1〜5（環境構築・MVP 実装・走行中 UI）はすべて完了済み。
法規チェック精度向上フェーズのタスク 1〜4 もすべて完了。
タスク A（experiment.py の v3 統一 + v1/v3 比較エンドポイント）も完了。
タスク B（GraphHopper の osm_way_id 有効化）も完了。

**現在はコード品質完成度の向上と評価実験データ確保のフェーズ。**
完了タスクの履歴は `docs/CHANGELOG.md` を参照。
システム構成と既存実装の説明は `docs/ARCHITECTURE.md` を参照。
環境構築手順は `docs/SETUP.md` を参照。

---

## このフェーズの方針

**研究上の現状認識：**

v3 ロジック（edge_id ベース判定 + 進行方向照合 + 右折 instruction 連動）と confidence
スコア導入により、v1 比で偽陽性が削減されたという定量結果は得られている。
ただし、宮治先生の「成果の難易度が低い」指摘に対する直接的反論材料としては、
「v1 比で違反検出が減った」だけでは弱い。

**必要な追加データ：**

1. **Ground truth との比較**：v1/v3 の検出が本物の違反かどうかの混同行列
2. **既存ナビとの比較**：Google Maps 自転車モードのルートとの違反検出数比較

これらを揃えることで「単に検出を減らしただけ」ではなく「正確性を高めた」
「既存ナビでは検出されていないリスクを定量的に示した」と主張できるようになる。

**フェーズ分け：**

- **フェーズ1（コード品質完成度）**：タスク C1〜C3。秋以降の論文執筆中にコード側で
  突っ込まれて手戻りしないよう、システム実装を本確定させる
- **フェーズ2（研究データ確保）**：タスク R1〜R2。フェーズ1完了後、または並行して
  着手。論文の主張を補強する実験データを揃える

フェーズ1を先行させる理由は、コード側の変更が研究データに影響する可能性があるため。
（例：二分探索化前後で結果が変わるはずはないが、interval[0] の解釈が誤っていた場合は
評価実験のやり直しが必要になる）

---

## フェーズ1：コード品質完成度

### タスク C1：way_id → 右折地点解決の二分探索化【優先度：中】

#### 背景

`route_analyzer.py` の `_analyze_v3` で、二段階右折判定のために
「右折 instruction の `interval[0]` インデックスから進入先 way_id を解決する」
処理が、外側ループ × 内側ループの線形探索（O(N×M)）になっている。

```python
for idx in two_step_idxs:
    wid = None
    for seg in way_id_details:
        s, e, w = int(seg[0]), int(seg[1]), int(seg[2])
        if s <= idx <= e:
            wid = w
            break
    two_step_tags_arg.append(...)
```

現状の 15 O-D ペアでは問題ないが、論文の評価実験規模を拡大した場合や、
ground truth 評価で同一ルートを繰り返し評価する場合にボトルネックになる可能性がある。
`way_id_details` は `start_idx` 昇順の構造なので二分探索化が可能。

#### 変更対象ファイル

- `backend/services/route_analyzer.py`

#### 手順

1. `_analyze_v3` の冒頭近くで、`way_id_details` をソート済みとみなして
   `start_indices = [int(seg[0]) for seg in way_id_details]` を一度だけ構築
2. 線形探索ループを `bisect_right(start_indices, idx) - 1` による二分探索に置き換え
3. 戻ってきたインデックスから `way_id_details[k]` の `end_idx` と
   `idx` の包含関係をチェック（`start <= idx <= end`）
4. インデックスが範囲外、または end_idx を超えている場合は `wid = None`

#### 完了条件

- 15 O-D ペアでの実行結果（violations / comparison）が変更前と完全に一致する
- `_analyze_v3` の右折地点解決部分の計算量が O(N log M) になる
- 既存の動作確認シナリオ（渋谷→新宿、東京駅→渋谷）でリグレッションが起きない

---

### タスク C2：二段階右折 interval[0] の妥当性確認【優先度：高】

#### 背景

`_analyze_v3` では、右折 instruction の `interval[0]` インデックスを使い、
そのインデックスを含む way_id 区間のタグを「進入先 way のタグ」として
`check_two_step_turn` に渡している。

しかし GraphHopper の `instructions` における `sign=2/3` の地点が、

- 「曲がる**前**の交差点に到達した時点」なのか
- 「曲がった**後**の道路に入った時点」なのか

はバージョンによって挙動が異なるケースが報告されている。
現在使用している `israelhikingmap/graphhopper:latest` のバージョンで、
本当に「進入先 way」のタグを取得できているのかを目視確認する必要がある。

論文で「進入先 way のタグで二段階右折を判定する」と主張する以上、
ここの検証を済ませておかないと、評価実験データの妥当性が揺らぐ。

#### 変更対象ファイル

- `backend/services/route_analyzer.py`（必要なら修正）
- 検証用の一時スクリプト（リポジトリには残さなくてよい）

#### 手順

1. 渋谷→新宿のルートで実際に `POST /api/route` を実行し、レスポンスの
   `original_route.instructions` から `sign=2` または `sign=3` の地点を抽出
2. 抽出した `interval[0]` の値と、`details.osm_way_id` の各区間の
   `[start_idx, end_idx, way_id]` を突き合わせる
3. その `interval[0]` を含む way_id について、OpenStreetMap で実際にその way を
   検索し、地図上の位置が「右折先の道路」になっているかを目視確認する
   - 右折先の道路と一致 → 現状ロジック OK
   - 右折前の道路と一致 → `interval[0]` を `interval[1]` に変える、または
     `way_id_details` で `start_idx` が `interval[1]` より大きい最初の way を
     取得するロジックに変更する必要あり
4. 確認結果を `docs/ARCHITECTURE.md` の `route_analyzer.py` セクションに
   1〜2行追記する（「GraphHopper v12 では interval[0] が進入先 way の
   先頭インデックスを指すことを確認済み」など）

#### 完了条件

- 渋谷→新宿のルートで、すべての右折 instruction について
  `interval[0]` が指す way が「右折先の道路」と一致することを目視確認した
- 一致しなかった場合は修正コードを実装し、修正前後で違反検出数の差分を記録する
- `docs/ARCHITECTURE.md` に確認結果を追記した

---

### タスク C3：Vite の安定版固定【優先度：中】

#### 背景

現状 `frontend/package-lock.json` を見ると Vite が 8.0.8、`rolldown` を使う
実験的ブランチ（`rolldown-vite` 系統）になっている。卒研の実装期間中・
論文執筆期間中に破壊的変更が入ると、提出直前に「フロントが起動しない」
事故につながる。

研究としての貢献はフロントエンドのバージョンに依存しないので、
ここは安定版に固定して動作を凍結する。

#### 変更対象ファイル

- `frontend/package.json`
- `frontend/package-lock.json`（再生成）

#### 手順

1. `frontend/package.json` の Vite を `^7.x`（または `^6.x` の最新安定版）に変更
2. `frontend/node_modules` と `frontend/package-lock.json` を削除
3. `npm install` で再インストール
4. `npm run dev` で起動を確認、`http://localhost:5173` でアプリが表示されること
5. 渋谷→新宿のシナリオで、地図表示・違反マーカー・モード切り替え・GPS
   インジケーターが従来通り動作することを確認
6. スマートフォン LAN アクセス（HTTPS）も従来通り動作することを確認
   （`@vitejs/plugin-basic-ssl` の互換性も確認）

#### 完了条件

- Vite が安定版（7.x または 6.x の最新）に固定された
- 既存の動作確認シナリオでリグレッションが起きない
- HTTPS 経由のスマホアクセスも動作する

---

## フェーズ2：研究データ確保

フェーズ1完了後に着手する。タスク C2 で `interval[0]` の解釈が誤っていた
場合は、フェーズ2の評価実験を全てやり直すことになるため、必ず C2 を先に
完了させること。

### タスク R1：Ground truth による評価（人手判定との混同行列）【優先度：高】

#### 背景

現状の評価実験は「v1 vs v3 で違反検出数がどう変わったか」しか測れていない。
論文の主張を「正確性を高めた」「偽陽性を削減した」に強化するためには、
「人手で判定した本物の違反」と「システムの判定結果」を比較する必要がある。

これにより、v1 / v3 それぞれについて Precision / Recall / F1 を算出でき、
「v3 で Recall を維持しながら Precision を改善した」という主張が可能になる。

宮治先生の「成果の難易度が低い」指摘に対する直接的な反論材料になる。

#### 変更対象ファイル

- `backend/data/ground_truth.csv`（新規作成）
- `backend/routers/experiment.py`（混同行列出力エンドポイントを追加）
- `docs/ARCHITECTURE.md`（評価方法の説明を追加）

#### 手順

1. **対象ルートの選定（5〜10ペア）**：
   - `od_pairs.csv` から幹線道路中心・住宅街中心・混在型を均等に抽出
   - 1ルートあたり 1〜5km 程度の長さ
2. **Ground truth の作成**：
   - 各ルートで `POST /api/route` を v3 で実行し、レスポンスの violations と
     ルート全体の OSM way_id 列を取得
   - Google Maps と OpenStreetMap で実際の道路を確認しながら、各 way について
     「一方通行を逆走しているか」「右折時に二段階右折が必要な道路か」を人手判定
   - `ground_truth.csv` の列：`label`, `way_id`, `point_lat`, `point_lng`,
     `true_oneway_violation`, `true_two_step_required`, `notes`
3. **混同行列出力エンドポイントの実装**：
   - `POST /api/experiment/ground-truth/compare` を新設
   - `ground_truth.csv` を読み込み、各ルートを v1 と v3 で実行
   - way_id 単位で「ground truth の violation」と「システムの violation」を
     突き合わせ、TP / FP / FN / TN を集計
   - 出力 CSV の列：`label`, `algo_version`, `rule`, `TP`, `FP`, `FN`, `TN`,
     `precision`, `recall`, `f1`
4. **判定の境界条件を明文化**：
   - confidence < 0.7 の violations は「検出済み」とみなすか「未検出」とみなすか
   - 推奨：confidence ≥ 0.7 のみを「検出」とカウントし、< 0.7 は別途集計

#### 完了条件

- `ground_truth.csv` に 5〜10ルートぶんの人手判定結果が登録された
- `POST /api/experiment/ground-truth/compare` が混同行列付き CSV を返す
- v1 と v3 で Precision / Recall / F1 を算出でき、v3 が Precision を改善した
  ことが定量的に示せる
- 判定の境界条件（confidence しきい値の扱い）が `docs/ARCHITECTURE.md` に記載される

---

### タスク R2：Google Maps 自転車モードとの手動比較実験【優先度：高】

#### 背景

卒業研究提案書のフェーズ1で予定している「既存ナビゲーションサービスとの
比較評価」がまだ実装されていない。論文の主軸である
「既存ナビでは対応できていない法規違反リスク箇所を定量的に示す」という
主張のために、Google Maps 自転車モードのルートと本システムのルートを比較する
必要がある。

**API を使わず手動で実施する方針**：卒研の規模（5〜10 ペア）では、Google Maps
Directions API のセットアップ・課金設定・クライアント実装コストよりも、
人手でルートをなぞる方が短時間で完了する。また、ストリートビューや OSM を
併用した人間の判断（自転車専用道の有無など）も組み込めるため、評価の質が高い。

ground truth 判定（R1）と同じルートを対象にすることで、二度手間を避ける。

#### 役割分担

- **マサヤさん（手動作業）**：Google Maps での経路取得、ルートの目視チェック、
  違反箇所カウント、CSV への記録
- **Claude Code（実装）**：手動入力 CSV テンプレートの作成、集計エンドポイントの
  実装、論文用の集計表の生成

#### 変更対象ファイル

- `backend/data/google_comparison.csv`（手動入力用テンプレートを新規作成）
- `backend/routers/experiment.py`（集計エンドポイントを追加）
- `docs/ARCHITECTURE.md`（手動比較実験の手順を追加）

#### 手順

1. **手動入力 CSV テンプレートの作成（Claude Code）**：
   - `backend/data/google_comparison.csv` を新規作成
   - 列構成：
     - `label`：R1 と同じラベル
     - `road_type`：幹線道路中心 / 住宅街中心 / 混在型
     - `system_distance_m`：本システムの v3 ルート距離
     - `system_time_s`：本システムの所要時間
     - `system_violation_count`：本システムのルート上の違反数（v3）
     - `system_violation_count_high_conf`：confidence ≥ 0.7 の違反数
     - `google_distance_m`：Google Maps 自転車モードの距離（手動入力）
     - `google_time_s`：Google Maps の所要時間（手動入力）
     - `google_oneway_violation_count`：Google Maps ルート上の一方通行違反数
       （手動カウント）
     - `google_two_step_violation_count`：Google Maps ルート上の二段階右折
       違反数（手動カウント）
     - `route_overlap_pct`：本システムと Google Maps のルート重複率（目視概算、
       0〜100 で記入）
     - `screenshot_filename`：スクリーンショットのファイル名
     - `notes`：判定に迷った点、特記事項
   - 各列のサンプル行を 1 行入れて、入力例を示す

2. **マサヤさんによる手動ルート取得・判定**：
   - R1 で選定した 5〜10 ペアと同じ O-D で Google Maps の自転車モードを開く
   - 各ペアについて以下を記録：
     - Google Maps 上での距離・所要時間
     - スクリーンショットを `docs/screenshots/google_maps/` に保存
     - ルートを目視でなぞり、一方通行違反・二段階右折違反の箇所をカウント
       （OSM Wiki / ストリートビューで道路属性を確認）
     - 本システムのルートとの重複率を目視で 0〜100 概算
   - 並行して、本システムの値（`system_*` 列）も `POST /api/route` v3 で取得して記録

3. **集計エンドポイントの実装（Claude Code）**：
   - `POST /api/experiment/google-comparison/summary` を新設
   - `google_comparison.csv` を読み込み、以下を集計した CSV を返す：
     - 各ペアの「本システム vs Google Maps」の違反数差分
     - 全ペアでの平均距離差・平均違反数差
     - 「本システムでは回避できているが Google Maps では残っている違反」の総数
     - `road_type` 別の集計
   - エンドポイントは「読み込んで集計するだけ」の軽量実装（外部 API 呼び出しなし）

4. **論文用の集計**：
   - `road_type` 別に「本システムが Google Maps 比でいくつ違反を回避できたか」を
     表として整理
   - 「本システムのリルートが Google Maps では検出されていない違反を回避できて
     いる」ことを定量的に示す
   - 距離増加とのトレードオフ（縄野・間邊2020 の規格化旅行時間に相当する指標）
     も併せて記載

#### 完了条件

- `backend/data/google_comparison.csv` のテンプレートが作成され、列構成が
  明文化されている
- マサヤさんが 5〜10 ペアぶんの Google Maps 比較データを CSV に記入済み
- スクリーンショットが `docs/screenshots/google_maps/` に保存されている
- `POST /api/experiment/google-comparison/summary` が集計 CSV を返す
- 集計結果から「本システム vs Google Maps」の違反検出数差分が表として
  まとまっており、論文の章 5（評価実験）に転記可能な形になっている

---

## タスクの推奨実施順

1. **タスク C2**（interval[0] の妥当性確認）を最初に実施
   - 結果次第で C1 と R1 の前提が変わるため
2. **タスク C1**（二分探索化）と **タスク C3**（Vite 固定）を実施
   - 動作変更を伴わない品質改善なので影響が局所的
3. **タスク R1**（Ground truth 評価）を実施
   - C2 が完了している前提
4. タスク R2（Google Maps 手動比較）を実施
   - R1 と同じルートを対象にするため、R1 の人手判定と並行して実施するのが効率的
   - Claude Code はテンプレート CSV と集計エンドポイントのみ実装し、Google Mapsの手動操作はマサヤさんが行う

---

## 不変の制約（変更禁止）

- `check_sidewalk_violation` は **呼び出し禁止**（研究スコープ除外）
- `MapView.jsx` / `ViolationAlert.jsx` は **preparing モード専用**
- `RidingView.jsx` は **riding モード専用**
- モード切り替えはフロントエンド側のみ。バックエンド API への影響はない
- GraphHopper の `vehicle=bike` パラメータは旧仕様。**`profile=bike`** を使用すること
- リルート計算時は `ch.disable=True` が必須（CH モードでは custom_model 非対応）
- `route.py` は常に v3 固定。`algo_version` パラメータは追加しない
- `experiment.py` の v1/v3 切替構造は変更しない（論文の比較実験で使用中）

---

## 開発時の共通ルール

- 実装前に必ず対象ファイルを `view` で読み、既存実装を把握すること
- エラーが発生した場合は、エラーメッセージを解析して自律的に修正すること
- 大きな変更を加える前に、変更方針を要約してユーザーに確認すること
- 既存の動作確認シナリオ（渋谷→新宿、東京駅→渋谷）でリグレッションが起きないこと
- バックエンドの起動方法は `docs/SETUP.md` を参照
- タスク完了時は `docs/CHANGELOG.md` に追記する

---

## 参照ドキュメント

- `docs/ARCHITECTURE.md`: システム構成・既存実装の説明・OSM タグの判定ルール
- `docs/SETUP.md`: 環境構築手順（Docker・FastAPI・Vite）
- `docs/CHANGELOG.md`: 完了タスクの履歴
