# 自転車ナビゲーションシステム 開発指示書（性能向上フェーズ）

日本の交通法規に準拠した自転車ナビゲーションシステム。
卒業研究（青山学院大学・宮治研究室）のシステム実装プロジェクト。

---

## プロジェクト現状（2026-06-05）

UI 改善フェーズ（U1〜U2）およびフェーズ2の評価実験基盤（R1：Ground truth 比較、
R2：Google Maps 手動比較、F1：二段階右折のリルート除外）まで完了済み。
詳細は `docs/CHANGELOG.md` を参照。

- 残された UI タスク（U3：riding モードのフルスクリーン化、U4：スマホ実機テスト）
  は **本フェーズと並行ではなく、別フェーズで再着手する**。本フェーズの実装範囲外。
- R1/R2 の手動データ記入（Masaya さんによる Google Maps 比較・人手判定）は別タスクで
  継続中（Claude Code は触らない）。

**現在は性能向上フェーズ。** 主要機能（ルート生成・法規チェック・リルート）の
**速度** と **ルート品質** を両軸で改善する。論文の主張に「実用速度を達成」と
「法規準拠かつ走りやすいルート品質」の両方を載せる準備をする。

履歴詳細は `docs/CHANGELOG.md` を参照。
システム構成は `docs/ARCHITECTURE.md` を参照。

---

## このフェーズの方針

### 目的

1. **エンドツーエンドのレスポンス時間** を短縮し、スマホでの実用利用に耐える
   応答性を確保する（目標：渋谷→新宿クラスのルートで p50 < 2s、p95 < 4s）
2. **ルート品質** を高め、Google Maps 自転車モードと並べた際に「法規準拠かつ
   走りやすい」と評価できる水準に引き上げる
3. 上記2点を **論文の評価軸** として提示できるよう、改善前後の差分を手動で
   計測して記録する

### 設計方針

- **キャッシュ層を導入**：Overpass の way_id 単位の結果はプロセス内 LRU で再利用
  する（同一 way を含むルートでは Overpass 呼び出しを省略）
- **リルートの高速化**：oneway 違反検出時の GraphHopper 再リクエストにおける
  ペイロード・パラメータを見直し、不要な情報量を削減する
- **自転車インフラ優先**：`cycleway=lane/track` を持つ way を GraphHopper の
  `custom_model` で重み付けし、初回ルートから走りやすい経路を返す
- **進行方向照合の精度向上**：way 長が短い・カーブが多い区間で起こる偽陰性・
  偽陽性を抑え、`confidence=1.0` の比率を上げる

### 不変の制約（変更禁止）

- バックエンド API の **公開仕様** は変更しない（`POST /api/route` のリクエスト・
  レスポンス形式・フィールド名は維持）
  - 内部実装の `comparison.algo_version` 文字列の更新は許容（例：`v3` → `v4`）
- v3 判定ロジックの **設計意図**（edge_id ベース + 進行方向照合 + 右折 instruction
  限定 + confidence 3 段階）は維持する
- `check_sidewalk_violation` は引き続き呼び出さない
- experiment.py / batch エンドポイントの **公開エンドポイント名** は維持
- フロントエンドの UI レイアウト（U1/U2 で構築したボトムシート構造）は変えない
- localStorage / IndexedDB は引き続き使わない

---

## タスク一覧

タスク P（Performance：速度）と Q（Quality：品質）を交互に実施する。
P1 → Q1 → P2 → Q2 の順を推奨。

### タスク P1：Overpass キャッシュ層の導入【優先度：最高】

#### 背景

`get_way_tags_by_ids` は呼び出すたびに Overpass API への外部 HTTP リクエストを
発生させる。同じ way が複数のルート探索で繰り返し問い合わされる
（例：渋谷→新宿と東京駅→渋谷で共通する幹線道路）にもかかわらず、毎回
ネットワーク経由で再取得しているため、レスポンス時間の主要なボトルネックに
なっている。

プロセス内 LRU キャッシュを導入し、way_id 単位で `{tags, geometry}` を再利用する。

#### 変更対象ファイル

- `backend/services/overpass.py`（キャッシュ層を追加）
- `backend/services/route_analyzer.py`（変更不要の見込み。キャッシュは透過的）

#### 手順

1. **キャッシュインターフェースの設計**：
   - `way_id: int → {"tags": dict, "geometry": list}` の dict ベース LRU
   - 容量上限：10,000 件（way 1 件あたり ~1KB として 10MB 程度）
   - 実装は `functools.lru_cache` ではなく、`collections.OrderedDict` ベースの
     シンプルな自前 LRU（複数キーを一度に問い合わせる用途のため）
2. **`get_way_tags_by_ids` のキャッシュ対応**：
   - 受け取った `way_ids` のうち、キャッシュにあるものは即座に結果へ含める
   - キャッシュにないものだけを Overpass に問い合わせる
   - 取得した結果は逐次キャッシュへ格納（LRU 末尾へ）
   - 全件キャッシュヒット時は Overpass 呼び出しを完全にスキップ
3. **キャッシュ統計のロギング**：
   - 1 リクエストごとに `cache_hit=X, cache_miss=Y, overpass_called=Z` を info ログに出力
   - ログレベル DEBUG で個別 way_id のヒット状況も出せるようにする（既定は INFO）
4. **キャッシュ無効化エンドポイント（任意）**：
   - 不要であれば実装しない。Docker 再起動でキャッシュは自然に破棄される
5. **テスト**：
   - 渋谷→新宿を 2 回連続で叩き、2 回目の Overpass 呼び出し回数が 0 件に
     なることを手動で確認

#### 完了条件

- 同一 O-D ペアを 2 回叩いたとき、2 回目の way_id ベース Overpass 呼び出しが 0 件
- 起動直後の cold cache 時のレスポンス時間が劣化していない（許容範囲：+50ms 以内）
- ログで `cache_hit / cache_miss / overpass_called` が確認できる
- 既存の `POST /api/route` のレスポンスが変更前と同じ（リグレッションなし）

#### 計測方法（手動）

- `time curl -X POST http://localhost:8000/api/route -d '...'` を 2 回連続で実行
- 1 回目（cold） と 2 回目（warm）の `real` 時間を `docs/PERFORMANCE.md`（新規作成）に記録

---

### タスク Q1：自転車インフラ優先の custom_model【優先度：高】

#### 背景

現在の GraphHopper 設定は bike プロファイルのデフォルト重み付けに依存しており、
`cycleway=lane/track` を持つ way を積極的に選好していない。結果として、自転車レーンが
ある裏道よりも幹線道路を優先するケースが発生し、論文の「走りやすさ」軸で
Google Maps 自転車モードに見劣りする可能性がある。

GraphHopper の `custom_model` で `cycleway=lane/track` および
`highway=cycleway/path` に正のスピード倍率を与え、初回ルートから自転車インフラを
通る経路を優先させる。

#### 変更対象ファイル

- `graphhopper/config.yml`（`encoded_values` に `cycleway` を追加）
- `backend/services/graphhopper.py`（`custom_model` をリクエストに追加）
- `graphhopper/default-gh/`（グラフ再ビルドが必要）

#### 手順

1. **`graphhopper/config.yml` の encoded_values 拡張**：
   - 現状の `graph.encoded_values` に `cycleway` を追加
   - これにより GH のグラフに cycleway タグの値が encoded value として保存され、
     `custom_model` から参照可能になる
   - 設定変更後は `graphhopper/default-gh/` を削除して再ビルド
2. **`get_route` に custom_model を追加**：
   - 通常リクエストにも軽量な `custom_model` を含める
   - `priority` ルールで以下を優遇：
     - `cycleway == LANE` または `cycleway == TRACK`：×1.4
     - `road_class == CYCLEWAY` または `road_class == PATH`：×1.5
     - `road_class == PRIMARY`：×0.7（避けたいので係数を下げる）
     - `road_class == SECONDARY`：×0.85
   - `ch.disable=true` が必須になる点に注意（高速化用 CH の制約）。応答時間の
     悪化が許容範囲（< 200ms 程度）かを計測して判断する
3. **既存 O-D での影響確認**：
   - 渋谷→新宿、東京→渋谷など主要 5 ペアで距離・時間・違反数の変化を計測
   - `docs/PERFORMANCE.md` に比較表を記録
   - 距離が大幅に増える（+20% 以上）場合は係数を調整して再実験
4. **リルート時の custom_model も整合**：
   - `rerouter.py` 側の `custom_model` にも同じ priority ルールを足して、迂回時にも
     自転車インフラを優先させる
5. **論文用スクリーンショット**：
   - 改善前後のルート画像を 2〜3 ペアぶん残しておく（手動）

#### 完了条件

- `graphhopper/config.yml` に `cycleway` encoded value が含まれ、再ビルドが完了している
- `POST /api/route` のレスポンスで、`cycleway=lane/track` を含む way の通過率が
  改善前より上昇している（手動で 5 ペアぶん確認）
- 既存の違反検出ロジックがリグレッションなく動作する
- リルート時にも custom_model が反映される

#### 注意点

- `ch.disable=true` を恒久的に有効化すると応答時間が悪化する可能性がある。
  P1 のキャッシュ効果と相殺できる範囲かを必ず実測してから採用する
- custom_model の構文は GraphHopper v12 のドキュメントに準拠する
  （`speed[] / priority[]` の if-then 形式）

---

### タスク P2：リルート高速化【優先度：中】

#### 背景

oneway 違反検出時の `get_compliant_route` は GraphHopper を再度呼び出すが、
以下の点で改善余地がある：

- 通常ルートと同じ `details: [osm_way_id, road_class]` を要求しているが、リルート
  結果は表示用途のみのため詳細情報は不要
- `custom_model + areas` のブロック範囲（±0.001度 ≈ 約100m）が広すぎ、過剰迂回や
  GH の探索コスト増を招いている可能性
- リルート後にもう一度 Overpass・法規チェックを走らせていないので二重チェックは
  不要だが、コードを追って確認する

#### 変更対象ファイル

- `backend/services/rerouter.py`
- `backend/services/graphhopper.py`（リルート用の軽量リクエストを区別）

#### 手順

1. **リルートリクエストの details を最小化**：
   - リルート用には `details` を要求しないか、`road_class` だけに絞る
   - レスポンスサイズ削減で GH 内部の serialize コスト・ネットワーク転送量が下がる
2. **areas 半径の最適化**：
   - 現状 ±0.001度 ≈ 100m を ±0.0005度 ≈ 50m に縮小して試す
   - 渋谷→新宿で oneway 違反 2 件、東京→渋谷で 7 件など複数ケースで挙動確認
   - リルートが失敗するケースが増えるなら半径を戻す
3. **リルート全体の所要時間を計測**：
   - 改善前後で `get_compliant_route` の所要時間を `time.perf_counter` で計測し、
     `route_analyzer.py` の info ログに残す
4. **不要な処理がないか route_analyzer.py を確認**：
   - リルート後に Overpass や法規チェックが再走しないことをコードリーディングで確認
   - 走っていたら排除する（おそらく現状でも走っていないが念のため）

#### 完了条件

- リルート所要時間が改善前と同等以上の速度になっている（許容：±10%）
- リルート結果のルート距離が極端に変化していない（±5% 以内）
- 既存の動作確認シナリオ（渋谷→新宿、東京駅→渋谷）でリグレッションなし
- `docs/PERFORMANCE.md` にリルート時間の改善前後を記録

---

### タスク Q2：進行方向照合のロバスト化【優先度：中】

#### 背景

`check_oneway_violation` は way geometry の始点→終点ベクトルと travel_vector の
内積で逆走判定を行うが、以下の状況で精度が落ちる：

- way が長くカーブしているとき：始点→終点の直線ベクトルが実際の走行方向と乖離
- way が短いとき（< 20m）：travel_vector のノイズに対する内積符号が不安定
- ルートが way の一部しか通らないとき：start_idx〜end_idx 間の区間ベクトルでなく
  way 全体のベクトルを使っているため誤判定

これらに対応し、`confidence=1.0` の信頼度が出る範囲を広げる。

#### 変更対象ファイル

- `backend/services/route_analyzer.py`（`travel_vectors` 構築箇所）
- `backend/services/law_checker.py`（`check_oneway_violation` 内のベクトル計算）

#### 手順

1. **way ベクトルを「ルートが実際に通った区間」に限定**：
   - way 全体の geometry ではなく、`route_analyzer.py` で計算済みの
     `start_idx〜end_idx` 範囲だけを切り出して始点・終点ベクトルを構築する
   - way geometry のうち、travel_vector の起点・終点に最も近いノード 2 点を選ぶ
     簡易ヒューリスティックでもよい
2. **短い way（< 20m）への対処**：
   - 区間長を計算（Haversine など）し、20m 未満の場合は内積判定をスキップして
     `confidence=0.7` で違反登録する（現状の `oneway=yes` だけで判定）
3. **カーブ区間のベクトル分割**：
   - 区間ノードが 3 点以上ある場合、隣接ノード間のベクトルを連続で内積し、
     **過半数が逆向き** なら逆走と判定する（単一の始終点ベクトルではなく多数決）
4. **テストケースの確認**：
   - 既存の 15 O-D ペアで `confidence=1.0` の比率が改善前後でどう変化するか
     `experiment/batch/od-pairs/csv` を叩いて比較
   - 偽陽性・偽陰性が増えていないかを R1（ground truth）データと突き合わせて確認

#### 完了条件

- 15 O-D ペアで `confidence=1.0` の比率が改善前と同等以上（理想は +10pt 以上）
- ground truth と突き合わせて、True Positive 件数が改善前以上
- 既存の動作確認シナリオでリグレッションなし
- `docs/PERFORMANCE.md` に confidence 分布の改善前後を記録

---

## タスクの推奨実施順

1. **タスク P1**（Overpass キャッシュ層）を最初に実施
   - 後続タスクの計測でレスポンス時間のブレを抑えるため、最も効果の大きい
     キャッシュを先に入れる
2. **タスク Q1**（custom_model）を次に実施
   - グラフ再ビルドが必要なため、他タスクと並行しないこと
3. **タスク P2**（リルート高速化）
4. **タスク Q2**（進行方向照合のロバスト化）

各タスク完了時に `docs/PERFORMANCE.md`（新規作成）に改善前後の計測値を追記する。
新規ファイル作成時はテンプレートを以下のとおりにする（最初のタスクで作成）：

```markdown
# 性能改善ログ

各タスクの改善前後の手動計測値を記録する。

## タスク P1：Overpass キャッシュ層

| 計測項目 | 改善前 | 改善後 |
|---|---|---|
| 渋谷→新宿 1回目 | xx.x s | xx.x s |
| 渋谷→新宿 2回目（warm） | xx.x s | xx.x s |
| Overpass 呼び出し回数（warm） | x 回 | 0 回 |
```

---

## 開発時の共通ルール

- 実装前に必ず対象ファイルを `view` で読み、既存実装を把握すること
- バックエンド API の公開仕様は変更しない（リクエスト・レスポンスのフィールド名・
  形式を維持）
- 既存の動作確認シナリオ（渋谷→新宿、東京駅→渋谷）でリグレッションが起きないこと
- localStorage や IndexedDB は使わない
- 大きな変更を加える前に、変更方針を要約してユーザーに確認すること
- バックエンドの起動方法は `docs/SETUP.md` を参照
- タスク完了時は `docs/CHANGELOG.md` に追記する
- 性能の計測値は `docs/PERFORMANCE.md` に追記する（タスク P1 で新規作成）

---

## 参照ドキュメント

- `docs/ARCHITECTURE.md`: システム構成・既存実装の説明
- `docs/SETUP.md`: 環境構築手順（Docker・FastAPI・Vite）
- `docs/CHANGELOG.md`: 完了タスクの履歴
- `docs/PERFORMANCE.md`: 本フェーズの性能改善ログ（P1 で新規作成予定）

---

## このフェーズ完了後の予定

性能向上（P1〜P2、Q1〜Q2）が完了したら、以下のいずれかに移行する想定：

- **UI 改善フェーズ再開**：U3（riding モードのフルスクリーン化 + heading-up）、
  U4（スマホ実機テスト）
- **論文執筆フェーズ**：実験結果と性能改善の数値を論文セクションに反映

どちらに進むかは性能改善の成果を踏まえて判断する。
