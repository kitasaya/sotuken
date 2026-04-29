# 自転車ナビゲーションシステム MVP 開発指示書

## プロジェクト概要

日本の交通法規に準拠した自転車ナビゲーションシステムの MVP を構築する。
卒業研究（青山学院大学・宮治研究室）のシステム実装フェーズ。

**MVP のゴール：**
「出発地・目的地を入力すると、逆走禁止（`oneway` タグ）を考慮してリルートした経路を地図上に表示する」

---

## システム構成

```
frontend/   React + Leaflet.js（地図表示・入力UI）
backend/    Python + FastAPI(ルーティング・法規判定ロジック）
graphhopper/ Docker でセルフホスト（ルーティングエンジン）
```

外部API:

- **Overpass API**（OSMタグ取得、無料）
- **GraphHopper Public API**（Dockerが動かない場合のフォールバック）

---

## 開発ステップ（この順番で進めること）

### STEP 1: GraphHopper を Docker で起動する

以下の手順でセットアップすること。

1. プロジェクトルートに `graphhopper/` ディレクトリを作成
2. 関東エリアの OSM データをダウンロード:

   ```
   https://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
   ```

   を `graphhopper/` に保存（ファイル名: `kanto-latest.osm.pbf`）

3. `graphhopper/config.yml` を作成:

   ```yaml
   graphhopper:
     datareader.file: /data/data.pbf
     graph.location: /data/graph-cache
     graph.encoded_values: car_access, car_average_speed, country, road_class, roundabout, max_speed, foot_access, foot_average_speed, foot_priority, foot_road_access, hike_rating, bike_access, bike_average_speed, bike_priority, bike_road_access, bike_network, mtb_rating, ferry_speed, road_environment
     import.osm.ignored_highways: motor, trunk
     profiles:
       - name: bike
         custom_model:
           speed:
             - if: "true"
               limit_to: "bike_average_speed"
           priority:
             - if: "!bike_access"
               multiply_by: "0"

   server:
     application_connectors:
       - type: http
         port: 8989
         bind_host: 0.0.0.0
     admin_connectors:
       - type: http
         port: 8990
         bind_host: 0.0.0.0
   ```

4. `docker-compose.yml` をプロジェクトルートに作成:

   ```yaml
   version: "3"
   services:
     graphhopper:
       image: israelhikingmap/graphhopper:latest
       ports:
         - "8989:8989"
       volumes:
         - ./graphhopper:/data
       environment:
         - JAVA_OPTS=-Xmx4g -Xms1g
       command: >
         --url https://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
         --host 0.0.0.0
         -c /data/config.yml
   ```

5. `docker-compose up` で起動し、`http://localhost:8989` にアクセスして動作確認

**確認コマンド:**

```bash
curl "http://localhost:8989/route?point=35.6762,139.6503&point=35.6895,139.6917&profile=bike&locale=ja"
```

JSONレスポンスが返ってきたら成功。

---

### STEP 2: FastAPI バックエンドを作成する

`backend/` ディレクトリに以下を作成すること。

**ファイル構成:**

```
backend/
  main.py
  routers/
    route.py
    geocode.py
    experiment.py
  services/
    graphhopper.py   # GHへのリクエスト
    overpass.py      # OSMタグ取得（バルク化・リトライ済み）
    law_checker.py   # 法規判定ロジック
    rerouter.py      # 再ルーティングロジック
    geocoder.py      # Nominatim ジオコーディング
  requirements.txt
```

**`requirements.txt`:**

```
fastapi
uvicorn
httpx
```

**起動確認:**

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

`http://localhost:8000/docs` で Swagger UI が開けばOK。

---

### STEP 3: React フロントエンドを作成する

**セットアップ:**

```bash
npm create vite@latest frontend -- --template react
cd frontend
npm install
npm install leaflet react-leaflet axios
```

**ファイル構成（`src/` 以下）:**

```
src/
  App.jsx
  components/
    MapView.jsx        # Leaflet地図表示（元ルート/準拠ルート/違反マーカー）
    SearchForm.jsx     # 住所入力タブ・緯度経度入力タブ
    ViolationAlert.jsx # 法規違反・推奨情報の表示
  api/
    route.js           # バックエンドAPIクライアント
```

**起動確認:**

```bash
cd frontend
npm run dev
```

`http://localhost:5173` でアプリが表示されればOK。

---

### STEP 4: 動作確認シナリオ

以下の住所でテストすること:

- 出発地: `渋谷駅`
- 目的地: `新宿駅`

**期待する動作:**

1. 住所入力でジオコーディングされ、地図上にルートが表示される
2. 一方通行違反箇所に赤いマーカーと警告が表示される
3. 法規準拠ルート（青・太線）と元の最短ルート（グレー・細線）が両方表示される
4. リルートした場合「⚡ 法規に合わせてルートを変更しました」と表示される

---

## 研究スコープ：対応する法規チェック

### ✅ 対象とする法規（経路選択に関わるもの）

| チェック項目     | 使用OSMタグ                                  | 判定条件                                             |
| ---------------- | -------------------------------------------- | ---------------------------------------------------- |
| 一方通行違反     | `oneway=yes`                                 | 逆走ルートを排除（`oneway:bicycle=no` で自転車除外） |
| 二段階右折義務   | `highway=primary/secondary` または `lanes≥3` | 幹線道路・多車線で違反フラグ                         |
| 自転車レーン推奨 | `cycleway=lane/track`                        | 違反ではなく推奨情報として返却                       |

### ❌ 対象外とする法規（研究スコープ除外）

| チェック項目              | 除外理由                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 歩道走行（`sidewalk=no`） | 「走っているのが歩道か車道か」は利用者が現場で視認して判断できるため、ナビの経路選択で排除すべき問題ではない。また `sidewalk=no` は「歩道が存在しない道路」という物理的状態を示すタグであり、「自転車の歩道走行が法的に禁止されている」を意味しない（タグの意味と法規の方向が逆）。実装済みだが研究スコープから除外。コードは `law_checker.py` に `check_sidewalk_violation` として残すが、`route.py` および `experiment.py` から呼び出しをコメントアウトすること。 |

---

## 既知の制限・注意事項（論文の限界として記載予定）

- OSMの `oneway` タグが付いていない道路は検出できない
- Overpass APIへのリクエストはレートリミットあり（本番では間引きが必要）
- GraphHopperのセルフホスト初回起動はOSMデータのビルドに時間がかかる（関東は30分〜1時間程度）
- GraphHopperが起動しない場合は Public API（`https://graphhopper.com/api/1/route`）にAPIキーを取得して差し替えること
- リルート時は `ch.disable=True`（Dijkstra/A\*）のため法規準拠ルート計算が遅い（数秒〜十数秒）
- Overpass一括クエリの座標対応は「wayの中心座標との最近傍マッチング」のため、
  長い道路セグメントでは中心が遠い場合がある（精度上の限界）
- Overpass 公開サーバーが全滅した場合は法規チェックをスキップして元ルートを返す
  （論文の限界として記載予定）

---

## 実装済みの修正・注意事項

### GraphHopperのAPIパラメータ変更（重要）

現在使用している `israelhikingmap/graphhopper:latest` は新バージョンのため、
`vehicle=bike` ではなく **`profile=bike`** を使用すること。

`services/graphhopper.py` では以下のパラメータを使用済み:

```python
"profile": "bike",   # ← vehicle: "bike" は古い書き方、動かない
"points_encoded": "false",
```

`details` パラメータ（`road_class` 等）は現在の config.yml では未設定のため除去済み。

### Overpass APIのエラーハンドリング

Overpass API は負荷状況によって 504 タイムアウトを返すことがある。
`services/overpass.py` に `_post_with_retry` を実装済み。
3エンドポイント（overpass-api.de → kumi.systems → maps.mail.ru）を順次試行し、
全滅した場合は空結果を返してチェックをスキップ・ルートのみ表示する。

### Overpass バルク化による高速化

逐次呼び出し（約60秒）→ Union構文による一括クエリ（3.4秒）に改善済み。
`services/overpass.py` の `get_bulk_way_tags` を使用すること。
法規チェック関数は `tags_list` 引数でタグを受け取る形式に統一済み。

### バックエンドの起動方法（Windows）

`uvicorn` を PowerShell で独立プロセスとして起動すること（ターミナルを閉じても継続動作）:

```powershell
Start-Process -FilePath 'python' -ArgumentList '-m uvicorn main:app --reload --port 8000' -WorkingDirectory 'C:\Users\masa2\Desktop\卒研\bicycle-navi\backend' -WindowStyle Hidden
```

または通常のターミナルで:

```bash
cd backend
python -m uvicorn main:app --reload --port 8000
```

---

## 現在の進捗（2026-04-23時点）

### 完了済み ✅

- **STEP 1**: GraphHopper を Docker で起動（`http://localhost:8989`）
  - `graphhopper/config.yml` カスタム設定済み（bike プロファイル）
  - OSM 関東データ（`kanto-latest.osm.pbf`）ビルド済み
- **STEP 2**: FastAPI バックエンド実装・稼働中（`http://localhost:8000`）
- **STEP 3**: React フロントエンド実装・稼働中（`http://localhost:5173`）
- **STEP 4**: 動作確認完了
  - 渋谷→新宿で青いルートが地図上に表示される
  - 一方通行違反箇所に赤いマーカーが表示される（渋谷→新宿で2件検出）
  - 違反内容のポップアップ表示も動作
- **法規チェック①**: 一方通行違反（`oneway=yes`）実装・動作確認済み（2026-04-12）
- **法規チェック②**: 自転車レーン推奨（`cycleway=lane/track`）実装済み（2026-04-12）
  - 推奨情報として `recommendations` フィールドで返却
- **法規チェック③**: 二段階右折要否判定実装済み（2026-04-12）
  - `highway=primary/secondary` または `lanes>=3` で違反フラグ
  - 渋谷→新宿: violations 5件（oneway×4, two_step_turn×1）
  - 東京駅→渋谷: violations 7件（oneway×6, two_step_turn×1）← 幹線道路で検出確認
- **歩道走行チェック**: `check_sidewalk_violation` を実装したが研究スコープ除外（2026-04-23）
  - `backend/services/law_checker.py` に関数は残存
  - `backend/routers/route.py` および `backend/routers/experiment.py` の呼び出しはコメントアウト済み
  - 除外理由：歩道走行は利用者が現場で視認判断できる問題であり、経路選択系の法規に該当しない
- **リルート**: 違反エッジ除外による再ルーティング実装済み（2026-04-12）
  - GraphHopper の `custom_model + areas` で違反座標付近（±0.001度≈100m）をブロック
  - `ch.disable=True` が必要（CHモードでは custom_model 非対応のため）
  - 渋谷→新宿: 元4617m → 法規準拠4922m（+305m迂回）を確認
- **評価実験用バッチ出力**: 複数ルートの比較データをCSV/JSONで出力する機能実装済み（2026-04-13）
  - `POST /api/experiment/batch`（JSON）
  - `POST /api/experiment/batch/csv`（CSVダウンロード）
- **ジオコーディング**: 住所・地名入力対応（Nominatim API）実装・動作確認済み（2026-04-13）
- **Overpass高速化**: バルク化・並列化により約60秒 → 3.4秒に改善済み（2026-04-14）

### 次に取り組むタスク

- [ ] **STEP 5: UIモード分離（走行中モード・出発前/停車中モード）の実装** ← 次フェーズ最優先
- [ ] 法規チェックの追加（一時停止・左側通行等）
- [ ] バッチ実験エンドポイントのテストケース拡充
- [ ] 精度検証・デバッグ

---

## STEP 5: UIモード分離の実装（次フェーズ・最優先）

### 背景：先生の指摘と調査結果

宮治先生から以下3点の指摘を受け、走行中UIの設計を見直すことになった（2026-04-28）。

1. アクションの形が視覚的にするものだと扱いづらい（自転車に取り付けるのも危険）
2. バイク・自転車用ナビは走行中は矢印しか出さず、それ自体は法に抵触しない
3. 適用する方法もあるのだということは示せたほうが良い

#### 法的根拠の確認

- スマホホルダーで自転車に固定すること自体は道路交通法上の違反ではない
- 違反になるのは「保持（手で持って運転）」と「注視（固定していても画面を継続的に見続ける）」の2点
- 道路交通法第71条第5号の5に「画像表示用装置に表示された画像を注視しないこと」と明記
- 「2秒以上の画面注視」が違反と判断される目安として広く認識されている
- ホルダーに固定したスマホで音声ナビを利用するのは合法

#### 業界標準のUI

ヤマハ「つながるバイクアプリ」、パイオニア「MOTTO GO」、Yahoo! MAP自転車向け、Google Maps、Beeline Moto IIなど、バイク・自転車ナビは「ターンバイターン方式の矢印表示＋音声案内」が業界標準となっている。「音声中心、画面はチラ見で済む情報密度」という方針が確立されている。

### 設計方針：2モード設計

本システムのUIは、利用シーンに応じて2つのモードに分けて設計する。

#### 走行中モード（`mode=riding`）

走行中の運転者が安全に利用できるUI。注視せずに済む情報密度に絞り込む。

**画面表示**

- 大きな矢印1つで次の進路を示す（直進・左折・右折・二段階右折）
- 次の交差点までの距離を大きな数字で表示
- 法規違反が発生する交差点では、矢印に警告マーク（黄色枠など）を重ねる
- 二段階右折が必要な交差点では専用アイコンを表示
- 地図表示は最小限（現在地と次の数十メートルの経路のみ）
- 詳細情報（違反箇所一覧・ルート全体）は表示しない

**音声案内**

- 主要な案内手段として音声を使用
- 交差点の手前で「○m先、二段階右折です」のように事前通知
- 二段階右折の手順を音声で案内
- 一方通行回避・歩道回避時には理由も簡潔に音声で説明（例：「一方通行のため迂回します」）
- Web Speech API（SpeechSynthesis）を使用

#### 出発前・停車中モード（`mode=preparing`）

出発前の経路確認や停車中の詳細確認に使うUI。情報密度は高くてよい。
**現在の `MapView.jsx` / `ViolationAlert.jsx` の機能はこちらに集約する。**

**画面表示**

- ルート全体を地図上に表示（現状維持）
- 法規違反リスク箇所をピン表示（現状維持）
- 二段階右折が必要な交差点の手順をステップ形式で表示（新規）
- 信号種別（車両用・歩行者用）の案内を交差点ごとに表示（新規）
- 違反一覧パネルで `violations` / `recommendations` をすべて確認できる（現状維持）

#### モード切り替え

GPS速度による自動切り替えと手動切り替えを併用する。

- デフォルト：時速5km/h以上で `riding` 、停止5秒以上で `preparing` に自動遷移
- 画面右上にモード切り替えボタンを設置（手動操作後はN分間自動切り替えを無効化）
- Geolocation APIの `position.coords.speed` を利用

### 実装タスク（フェーズ分け）

#### フェーズ5-1：UIモードの基本実装（優先度：高）

- [ ] `frontend/src/App.jsx` に `mode` state（`riding` / `preparing`）を追加
- [ ] `frontend/src/components/ModeSwitcher.jsx` を新規作成（モード切り替えボタン）
- [ ] 走行中モード画面 `frontend/src/components/RidingView.jsx` を新規作成
  - [ ] 大きな矢印コンポーネント（直進・左折・右折・二段階右折アイコン）
  - [ ] 次の交差点までの距離表示
  - [ ] 地図表示の最小化（現在地周辺のみ）
- [ ] 出発前・停車中モード画面：既存の `MapView.jsx` / `ViolationAlert.jsx` をこのモードに集約
- [ ] バックエンドのレスポンスから「次の交差点情報」を取得するロジックを追加
  - [ ] GraphHopperの `instructions` レスポンス（ターンバイターンデータ）を活用

#### フェーズ5-2：音声案内の実装（優先度：高）

- [ ] `frontend/src/services/voiceGuide.js` を新規作成（Web Speech API ラッパー）
- [ ] 交差点手前での事前通知ロジック（GPS位置と次の交差点距離で判定）
- [ ] 二段階右折手順の音声ガイド文言を確定
- [ ] 一方通行回避・歩道回避時の理由説明（バックエンドから理由文字列を返すよう調整）
- [ ] 音声案内のON/OFF切り替え

#### フェーズ5-3：モード自動切り替え（優先度：中）

- [ ] Geolocation API で現在地と速度を取得（`position.coords.speed`）
- [ ] しきい値判定ロジック（5km/h以上で走行中モード、停止5秒以上で停車中モード）
- [ ] 手動切り替えとの優先順位ルール（手動切り替え後はN分間自動切り替え無効）

#### フェーズ5-4：UIの細部調整（優先度：中）

- [ ] 走行中モードのアイコン・配色設計（視認性重視）
- [ ] 警告マークのデザイン（法規違反リスク箇所）
- [ ] フォントサイズ調整（走行中モードは大きく）
- [ ] スマートフォン対応UI（既存タスクと統合）

### 設計判断が必要なポイント（要相談）

実装着手前に、宮治先生に相談するか自分で方針を決める必要がある。

1. **モード切り替えの方式**：GPS自動・手動・両方併用のどれを採用するか（暫定：両方併用）
2. **音声案内の文言**：二段階右折の手順説明の言い回し（「左に方向転換」が適切か）
3. **法規違反リスク箇所の警告表現**：色・アイコン・音声の組み合わせをどう設計するか
4. **走行中モードでの最小情報密度**：地図を完全に隠すか、極小表示で残すか
5. **実装範囲の優先順位**：5-1〜5-2を確実に作り込むか、5-3まで広げるか

### 研究上の位置付け

本UI設計は、**研究の評価対象ではないが、研究成果が実適用可能であることを示すための設計要件**として位置付ける。

- 評価実験の主軸：複数の出発地・目的地ペアでの既存ナビとの経路比較（ルート生成の妥当性検証）
- UI設計の役割：研究成果が実走行に適用可能であることを示す材料
- 論文への反映：手法の章で「実走行に適用可能なUI設計を行った」と記載し、走行中UIのスクリーンショット・設計方針を提示する

---

## Claude Code への指示

STEP 1〜4 は**実装・動作確認済み**。次回からは **STEP 5（UIモード分離）** に取り組むこと。
各タスクを実装する前に必ず現在のファイルを読み、既存の実装を把握してから変更すること。
エラーが発生した場合は、エラーメッセージを解析して自律的に修正すること。

**重要**: `check_sidewalk_violation` は `law_checker.py` に定義されているが、
研究スコープ除外のため `route.py` および `experiment.py` から呼び出してはならない。
誤って呼び出しを復活させないよう注意すること。

**STEP 5 実装時の注意**:

- 既存の `MapView.jsx` / `ViolationAlert.jsx` は削除せず、出発前・停車中モードのコンポーネントとして再配置すること
- 走行中モードは新規コンポーネント（`RidingView.jsx`）として作成し、既存UIと共存させること
- モード切り替えはあくまでフロントエンド側の表示制御。バックエンドAPIへの影響はない
- 音声案内の文言は、設計判断ポイント2が確定するまで暫定文言で実装してよい
