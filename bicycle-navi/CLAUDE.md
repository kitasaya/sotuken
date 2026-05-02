# 自転車ナビゲーションシステム 開発指示書

日本の交通法規に準拠した自転車ナビゲーションシステム。
卒業研究（青山学院大学・宮治研究室）のシステム実装プロジェクト。

---

## プロジェクト現状（2026-05-02）

STEP 1〜5（環境構築・MVP実装・走行中UI）はすべて完了済み。
法規チェック精度向上フェーズのタスク1〜4もすべて完了。

**現在は評価実験の実施フェーズ。**
完了タスクの履歴は `docs/CHANGELOG.md` を参照。
システム構成と既存実装の説明は `docs/ARCHITECTURE.md` を参照。
環境構築手順は `docs/SETUP.md` を参照（再構築時のみ）。

---

## 研究上の最重要課題：法規チェックの偽陽性削減

現状の `backend/services/law_checker.py` は、ルート上の点を最大10点サンプリングし、
各点の最寄り way の OSM タグだけで違反判定している。
このため、以下の偽陽性が発生する：

1. **並行way問題**：脇道や上下分離した一方通行ペアの反対側 way のタグを誤って拾う
2. **進行方向未照合問題**：`oneway=yes` の道路を順方向に走っていても「逆走」と判定される
3. **二段階右折の過検出問題**：幹線道路を直進・左折しているだけでも「二段階右折違反」と判定される
4. **サンプリング粒度問題**：10点固定では短い違反区間を見逃すか、無関係な区間を誤検出する

これらは「成果の難易度が低い」という宮治先生の指摘に対する直接的な改善材料でもある。
edge_id ベース判定 + 進行方向照合 + instruction連動判定 で解決する。

---

## 完了済みタスク（2026-05-02）

タスク1〜4はすべて実装完了。詳細は `docs/CHANGELOG.md` を参照。

- **タスク1（完了）**: oneway 判定に進行方向照合 + 自転車除外タグ対応
- **タスク2（完了）**: 二段階右折判定を右折 instruction に限定
- **タスク3（完了）**: 違反判定に confidence スコア（0.4/0.7/1.0）を導入
- **タスク4（完了）**: バッチ実験テストケース拡充（関東圏 15 O-D ペア・`od_pairs.csv`）

---

## 現役タスク（優先度順）

実装着手前に **必ず** 対象ファイルを `view` で読み、既存実装を把握すること。

### ~~タスク1: oneway 判定に進行方向照合を追加【完了】~~

`oneway=yes` の道路でも順方向に走っていれば違反ではない。
way の進行方向とルートの進行方向の内積を取って判定する。

**変更対象ファイル：**
- `backend/services/overpass.py`
- `backend/services/law_checker.py`

**手順：**
1. `overpass.py` で way 取得時に `out geom;` または `out body geom;` で node 列（geometry）を取得
2. `law_checker.py` の `check_oneway_violation` を以下のロジックに変更：
   - way の geometry の始点→終点ベクトルを計算
   - ルート上の該当区間の進行方向ベクトルを計算
   - 内積が負なら逆走候補
   - `oneway=-1` の場合は判定を反転
   - `oneway:bicycle=no`、`cycleway=opposite`、`cycleway=opposite_lane`、`cycleway=opposite_track` のいずれかがあれば違反としない
3. ベクトル計算は単純な2次元内積でよい（測地線補正は不要）

**完了条件：**
- `oneway=yes` の道路を順方向に走った場合、違反として検出されない
- 逆方向に走った場合のみ違反として検出される

---

### ~~タスク2: 二段階右折判定を右折 instruction に限定【完了】~~

二段階右折は「右折時のみ」の義務。直進・左折・通過時には判定しない。

**変更対象ファイル：**
- `backend/services/law_checker.py`
- `backend/routers/route.py`

**手順：**
1. GraphHopper の `instructions` 配列から `sign=2`（TURN_RIGHT）または `sign=3`（TURN_SHARP_RIGHT）の地点を抽出
2. `check_two_step_turn` を「右折 instruction の地点で、かつ進入する道路が `highway=primary/secondary` または `lanes>=3` の場合のみ違反フラグ」に変更
3. instruction の地点座標から、進入元 way ではなく **進入先 way** のタグを参照する点に注意

**完了条件：**
- 幹線道路を直進・左折するだけのルートで二段階右折違反が検出されない
- 幹線道路で右折するルートでのみ違反として検出される

---

### ~~タスク3: 違反判定に confidence スコアを導入【完了】~~

判定の確実性に応じてスコアを付け、UI と評価実験で活用する。

**変更対象ファイル：**
- `backend/services/law_checker.py`
- `backend/routers/route.py`
- `backend/routers/experiment.py`
- `frontend/src/components/ViolationAlert.jsx`
- `frontend/src/components/MapView.jsx`

**スコア定義：**
| 条件 | confidence |
|---|---|
| edge_id 一致 + 進行方向照合済み + 自転車除外タグ確認済み | 1.0 |
| edge_id 一致のみ | 0.7 |
| 近傍 way 推定（フォールバック） | 0.4 |

**手順：**
1. `law_checker.py` の各違反辞書に `confidence: float` フィールドを追加
2. UI 側で confidence ≥ 0.7 と < 0.7 を色分け（確実な違反は赤、疑わしい違反は橙など）
3. バッチ実験では confidence 別の集計列を CSV に追加
4. 論文ではこのスコア体系自体を「タグベース近似判定の限界を定量化する手法」として位置付ける

**完了条件：**
- レスポンスの violations / recommendations に confidence が含まれる
- フロントエンドで confidence による表示分けが動作する
- バッチ CSV に confidence 別カウントが出力される

---

### ~~タスク4: バッチ実験エンドポイントのテストケース拡充【完了】~~

タスク1〜4の効果を定量比較するために、O-D ペアの拡充を行う。

**変更対象ファイル：**
- `backend/routers/experiment.py`
- 新規 CSV：`backend/data/od_pairs.csv`（関東圏の複数 O-D ペア）

**手順：**
1. 関東圏で道路環境の異なる 10〜20 ペアの O-D を選定（幹線道路中心・住宅街中心・混在型）
2. `od_pairs.csv` として保存
3. バッチエンドポイントで CSV を読み込んで一括実行できるようにする
4. 出力 CSV に「タスク1〜4 適用前後の違反数差分」を残せる構造にする

---

## 不変の制約（変更禁止）

- `check_sidewalk_violation` は **呼び出し禁止**（研究スコープ除外）
  - 関数定義は `law_checker.py` に残してよいが `route.py` / `experiment.py` から呼んではならない
  - 除外理由：歩道走行は利用者が現場で視認判断できる問題であり、経路選択系の法規に該当しない
- `MapView.jsx` / `ViolationAlert.jsx` は **preparing モード専用**
- `RidingView.jsx` は **riding モード専用**
- モード切り替えはフロントエンド側のみ。バックエンド API への影響はない
- GraphHopper の `vehicle=bike` パラメータは旧仕様。**`profile=bike`** を使用すること
- リルート計算時は `ch.disable=True` が必須（CH モードでは custom_model 非対応）

---

## 開発時の共通ルール

- 実装前に必ず対象ファイルを `view` で読み、既存実装を把握すること
- エラーが発生した場合は、エラーメッセージを解析して自律的に修正すること
- 大きな変更を加える前に、変更方針を要約してユーザーに確認すること
- 既存の動作確認シナリオ（渋谷→新宿、東京駅→渋谷）でリグレッションが起きていないこと
- バックエンドの起動方法は `docs/SETUP.md` を参照

---

## 参照ドキュメント

- `docs/ARCHITECTURE.md`: システム構成・既存実装の説明・OSM タグの判定ルール
- `docs/SETUP.md`: 環境構築手順（Docker・FastAPI・Vite）
- `docs/CHANGELOG.md`: 完了タスクの履歴
