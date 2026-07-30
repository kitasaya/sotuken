# 調査報告：way 138533178 の一方通行判定 原因切り分け

**調査日**: 2026-07-24
**対象**: 渋谷→新宿ルートで検出された oneway 違反（way 138533178）
**目的**: システムが「自転車を除く」補助標識付きの一方通行を誤って違反判定したのか
（仮説B：システムのバグ）、それとも OSM データ自体に自転車除外タグが存在しないために
仕様通り動作した結果なのか（仮説A：OSMデータ不備）を、事実に基づいて確定する。

**本調査では既存の実験データ（ground_truth.csv・google_comparison.csv 等）は一切変更していない。**

---

## Step 1: 現在のOSMタグ（Overpass API, 2026-07-24 取得）

クエリ:
```
[out:json][timeout:25];
way(138533178);
out tags meta;
```

生データ（全件、省略なし）:

```json
{
  "type": "way",
  "id": 138533178,
  "timestamp": "2025-01-11T11:46:47Z",
  "version": 10,
  "changeset": 161235942,
  "user": "setomaps",
  "uid": 21931672,
  "nodes": [1813858438, 6146808083, 1315058826],
  "tags": {
    "highway": "unclassified",
    "maxspeed": "20",
    "oneway": "yes",
    "surface": "asphalt"
  }
}
```

該当キーの有無:

| キー | 値 |
|---|---|
| `oneway` | `yes` |
| `oneway:bicycle` | **なし** |
| `bicycle` | **なし** |
| `bicycle:backward` / `bicycle:forward` | **なし** |
| `cycleway` / `cycleway:left` / `cycleway:right` | **なし** |
| `traffic_sign` / `traffic_sign:backward` | **なし** |
| `highway` | `unclassified` |
| `version` / `timestamp` / `changeset` | `10` / `2025-01-11T11:46:47Z` / `161235942` |

**現在のOSMデータには自転車除外に関するタグが一切存在しない。**

---

## Step 2: 実験時点（pbf ビルド時）のタグ

### 当初計画からの変更点（重要）

指示書では `kanto-latest.osm.pbf` を osmium で直接読む想定だったが、以下の理由で
実施不可能だったため、代替手段（下記）に切り替えた。経緯を明記する。

- `bicycle-navi/graphhopper/` 配下（docker-compose の bind mount 先）を確認したところ、
  `data.pbf` 自体は存在しなかった（`config.yml` と `default-gh/`（ビルド済みグラフ）のみ）。
  GraphHopper の docker イメージは起動時に pbf をダウンロードしてグラフを構築後、
  pbf ファイル自体は保持しない構成になっている。
- Geofabrik の `kanto-latest.osm.pbf` は常時更新される「最新」リダイレクトであり、
  実際に `HEAD` リクエストで確認したところ `kanto-260723.osm.pbf`（2026-07-23付）に
  リダイレクトされた。ダウンロードし直しても実験時点（2026-05-11）のスナップショットは
  再現できない。
- GraphHopper のビルド済みグラフ（`default-gh/`）は `properties.txt` の
  `graph.encoded_values` を見る限り、ルーティングに必要な符号化済み値
  （`bike_access` / `bike_road_access` / `road_class` 等）のみを保持しており、
  `oneway:bicycle` 等の生の OSM タグをそのまま保持していない。そのためグラフ
  キャッシュから直接タグを抽出することもできない。
- 上記より、osmium のインストール（未実施）は無意味と判断し実施しなかった。

### 代替手段：GraphHopper ビルドメタデータ + OSM編集履歴による論理的特定

`graphhopper/default-gh/properties.txt` に実験時点で使用した pbf のデータ基準時刻が
記録されている:

```
datareader.import.date=2026-05-12T06:16:40Z
datareader.data.date=2026-05-11T20:20:52Z
```

すなわち実験に使用された pbf は **2026-05-11T20:20:52Z 時点の OSM データのスナップショット**
である。

Step 3（下記）の編集履歴により、way 138533178 の最終更新（version 10）は
**2025-01-11T11:46:47Z** であり、それ以降 2026-07-24 現在に至るまで一切編集されていない
（現在のバージョンも version 10 のまま）ことが確認できる。

`2025-01-11T11:46:47Z`（最終編集）< `2026-05-11T20:20:52Z`（pbfデータ基準時刻）<
`2026-07-24`（現在）の間、当該 way に編集は発生していないため、
**実験時点の pbf のタグは Step 1 で確認した現在のタグと完全に一致する**
（`oneway=yes` のみ、自転車除外タグなし）と論理的に確定できる。

pbfタイムスタンプと現在タグの差分: **差分なし（編集が一度も発生していないため）**

---

## Step 3: OSM編集履歴（OSM API `history.json`、2026-07-24 取得）

全10バージョンの生データ（要約表。各バージョンのタグ全件は下記に列挙）:

| version | timestamp | changeset | user | oneway | oneway:bicycle | cycleway系 | その他タグ |
|---|---|---|---|---|---|---|---|
| 1 | 2011-11-27T05:51:25Z | 9963483 | damember | なし | なし | なし | highway=unclassified |
| 2 | 2012-03-11T08:16:29Z | 10939865 | chihalin | なし | なし | なし | highway=unclassified |
| 3 | 2012-07-05T14:32:42Z | 12121027 | nakanao | **yes**（新規追加） | なし | なし | highway=unclassified |
| 4 | 2013-07-23T05:24:57Z | 17056700 | futurumspes | yes | なし | なし | highway=unclassified |
| 5 | 2014-06-02T15:36:09Z | 22696505 | Антін Сартенченко | yes | なし | なし | highway=unclassified |
| 6 | 2015-08-01T01:56:05Z | 33022653 | etajin | yes | なし | なし | +admin_level=7, boundary=administrative |
| 7 | 2018-12-19T06:07:06Z | 65601029 | Oos1812 | yes | なし | なし | 同上 |
| 8 | 2021-03-31T13:40:51Z | 102059563 | 近場行き | yes | なし | なし | +maxspeed=20 |
| 9 | 2024-01-20T00:47:16Z | 146462353 | Rokomo35 | yes | なし | なし | admin_level/boundary 削除、maxspeed=20 |
| 10 | 2025-01-11T11:46:47Z | 161235942 | setomaps | yes | なし | なし | +surface=asphalt（**現行**） |

**結論：2011年の初版作成から2025年の最新版（version 10）まで、全10バージョンを通して
`oneway:bicycle` ・ `bicycle` ・ `cycleway`（`opposite` 系含む）等、自転車を一方通行の
例外とするタグは一度も追加されたことがない。** `oneway=yes` は version 3（2012年）で
追加されて以降、一貫して存在し続けている。

pbfタイムスタンプ（2026-05-11T20:20:52Z）との前後関係：最終編集（version 10,
2025-01-11）は pbf データ基準時刻より約4ヶ月前。pbf 基準時刻から現在までの間にも
編集は発生していない（version は現在も10のまま）。

---

## Step 4: システムのタグ解釈ロジックの単体確認

**スキップ。** 指示書の条件「Step 2 で pbf 側に自転車除外タグが存在した場合のみ実施」
に該当しないため（Step 2/3 の結果、pbf 側にも自転車除外タグは存在しなかった＝仮説Aが
確定したため）、単体テストは実施しなかった。

（参考：`services/law_checker.py` の該当ロジックは `check_oneway_violation` 内、
`oneway:bicycle == "no"` および `cycleway in ("opposite", "opposite_lane",
"opposite_track")` の場合に違反としない設計になっている。今回のタグセットには
両条件に該当するタグが存在しないため、上記ロジックが正しく実装されていたとしても
結果は変わらない。）

---

## Step 5: 他の17件（他の O-D ペアで検出された oneway 違反）のタグ一覧

### 「18件」の内訳についての確認事項

指示書は `google_comparison.csv` から「oneway違反として検出された全18件の way_id を
抽出」としていたが、同ファイルには way_id 列は存在しない（集計値のみ）。調査の結果、
**`google_comparison.csv` の `google_oneway_violation_count` 列を15 O-Dペア全件で合計すると
ちょうど18件になる**ことを確認した（下記）。これは **Google Maps が提示したルート**
（`google_routes_input.csv` の polyline）を `external_route_scorer.py` で採点した際に
検出された oneway 違反であり、**本システム自身が生成したルート上の違反ではない**
（本システム自身のルートでの oneway 違反は `google_comparison.csv` の
`system_violation_count` 列に別途集計されている。渋谷→新宿の場合、システム自身の
ルートでの違反1件は `two_step_turn` であり、oneway ではない）。

| label | google_oneway_violation_count |
|---|---|
| 渋谷→新宿 | 1 |
| 東京→渋谷 | 2 |
| 新宿→池袋 | 0 |
| 品川→東京 | 3 |
| 渋谷→六本木 | 0 |
| 下北沢→三軒茶屋 | 0 |
| 高円寺→中野 | 0 |
| 荻窪→阿佐ヶ谷 | 1 |
| 自由が丘→等々力 | 1 |
| 浦和→さいたま新都心 | 1 |
| 吉祥寺→三鷹 | 0 |
| 立川→国分寺 | 2 |
| 横浜→みなとみらい | 2 |
| 川崎→武蔵小杉 | 1 |
| 千葉→幕張本郷 | 4 |
| **合計** | **18** |

この18件の内訳（way_id・タグ）はどのファイルにも保存されていなかったため、
`google_routes_input.csv` に保存済みの Google ルート polyline を使い、
`services/external_route_scorer.py` の判定ロジックをそのまま呼び出して再現した
（判定ロジック自体は一切変更していない。Overpass 呼び出しのみを行う読み取り専用の
調査スクリプトを `/tmp` に作成して実行し、既存のリポジトリファイルは変更していない）。
再現した違反件数は15ペア全件で `google_comparison.csv` の値と完全に一致した
（合計18件、渋谷→新宿=1・東京→渋谷=2・品川→東京=3・荻窪→阿佐ヶ谷=1・
自由が丘→等々力=1・浦和→さいたま新都心=1・立川→国分寺=2・横浜→みなとみらい=2・
川崎→武蔵小杉=1・千葉→幕張本郷=4）。

各違反地点で最近傍マッチした way の現在のOSMタグ（2026-07-24時点、Overpass取得）:

| label | way_id | oneway | oneway:bicycle | bicycle | cycleway系 | highway | confidence | 備考 |
|---|---|---|---|---|---|---|---|---|
| 渋谷→新宿 | 138533178 | yes | なし | なし | なし | unclassified | 1.0 | **本報告の調査対象**（surface=asphalt, maxspeed=20） |
| 品川→東京 | 23690216 | yes | なし | なし | cycleway:left=no | unclassified | 1.0 | lanes=1, surface=paving_stones |
| 品川→東京 | 741785139 | yes | なし | なし | なし | tertiary_link | 1.0 | ランプ/連絡路、lanes=1 |
| 品川→東京 | 667962675 | yes | なし | なし | なし | trunk | 1.0 | 中央通り(国道15号)、foot=no（歩行者進入禁止、自転車除外とは無関係） |
| 荻窪→阿佐ヶ谷 | 80835360 | yes | なし | なし | なし | residential | 1.0 | タグ最小限 |
| 自由が丘→等々力 | 350353685 | yes | なし | なし | なし | service (alley) | 1.0 | FixMeノートあり「位置調整してください」（データ品質フラグ） |
| 浦和→さいたま新都心 | 325347768 | yes | なし | なし | なし | unclassified | 1.0 | source=Bing,2007-03（出典が2007年空撮、更新が古い可能性） |
| 立川→国分寺 | 853388885 | yes | なし | なし | なし | tertiary | 1.0 | maxspeed=50 |
| 立川→国分寺 | 1429406683 | yes | なし | なし | cycleway=lane(advisory) | tertiary | 1.0 | 立川南通り。cycleway=laneは順走用レーンで opposite系ではないため除外条件に非該当 |
| 横浜→みなとみらい | 28413948 | yes | なし | なし | なし | unclassified | 1.0 | 同一 way で2点検出。lanes=1（参考：CHANGELOGに記載の別件 way_id 28413951 とは別の way） |
| 川崎→武蔵小杉 | 263457845 | yes | なし | なし | なし | tertiary | 1.0 | 南武沿線道路、maxspeed=50 |
| 千葉→幕張本郷 | 22961575 | yes | なし | なし | なし | trunk | 1.0 | 国道126号。同一 way で2点検出 |
| 千葉→幕張本郷 | 142222817 | yes | なし | なし | なし | residential | 1.0 | source=bing 2007.04 |
| 千葉→幕張本郷 | 151808609 | yes | なし | なし | なし | unclassified | 1.0 | source=bing |
| 東京→渋谷 | 474601303 | yes | なし | なし | cycleway:left=no | tertiary | 1.0 | 東京タワー通り |
| 東京→渋谷 | 271979254 | yes | なし | なし | cycleway:left=shared_lane | primary | 1.0 | 外苑東通り(都道319号)。cycleway:left=shared_lane は順走レーンで opposite系ではないため除外条件に非該当 |

**この段階では判定の正誤を結論づけない。** 現地確認（ストリートビュー等）は
指示書の方針どおりユーザーが手動で行う。上記は「現在のOSMタグの一覧化」のみ。

観測として記録するに留める事実：18件全てで `oneway:bicycle` ・ `bicycle` ・
`cycleway`（`opposite`系）のいずれも存在しない。`cycleway:left=no` /
`cycleway:left=shared_lane` / `cycleway=lane` を持つ3件があるが、いずれも
`check_oneway_violation` の除外条件（`oneway:bicycle=="no"` または
`cycleway in ("opposite","opposite_lane","opposite_track")`）には該当しない
（`cycleway:left` は `cycleway` とは別キーであり、いずれの値も「反対方向通行可」を
意味する値ではない）。

---

## 切り分け結論

### way 138533178（渋谷→新宿）について

**仮説A（OSMデータ不備）が確定した。**

根拠：
1. 現在のOSMタグ（Step 1）に自転車除外タグが一切存在しない。
2. OSM編集履歴（Step 3）により、2011年の初版から2025年の最新版（version 10、
   現在も同一）まで、自転車除外タグが一度も追加されたことがないことが確認された。
3. 実験時点の pbf のデータ基準時刻（2026-05-11T20:20:52Z）は、当該 way の最終編集
   （2025-01-11T11:46:47Z）より後であり、かつそれ以降現在まで編集が発生していないため、
   実験時点のタグも Step 1 と完全に同一（自転車除外タグなし）であると論理的に確定できる。

したがって、現地の「自転車を除く」補助標識は一度も OSM に反映されておらず、
システムは実在する OSM データ（`oneway=yes` のみ）に対して仕様通りの判定を行った。
これはシステムのバグではなく、OSM データの完成度（現実の交通規制がすべて
マッピングされているわけではないこと）に起因する誤検出である。

### 他の17件について

Step 5 で一覧化した現在のOSMタグ上は、18件全てが way 138533178 と同型のパターン
（`oneway=yes` のみ、自転車除外タグなし）である。ただし、これらの箇所に実際に
「自転車を除く」等の現地標識が存在するか否かは現地確認（ストリートビュー等）が
必要であり、本調査では判定していない。したがってこの17件について「システムの
判定が正しいか誤りか」は本報告の時点では断定できない。追加で必要な情報は、
各地点のストリートビュー等による現地標識の確認である。

### 調査手法上の限界（透明性のため明記）

- Step 2 は実験時点の pbf ファイル自体を直接検証したものではなく、GraphHopper の
  ビルドメタデータと OSM 編集履歴から論理的に導出した結果である。ただし
  「対象期間内に編集が一度も発生していない」という事実により、この論理的導出は
  当該 way に関しては確実性が高い（編集がない以上、タグは変化しようがない）。
- Step 5 の18件は、当時 Overpass に問い合わせた際に選択されたのと同一の「最近傍 way」
  である保証はない（`external_route_scorer.py` は座標ベースの最近傍マッチであり、
  Overpass 側の道路網が現在までに変化している可能性がある）。ただし件数は
  `google_comparison.csv` の記録値と15ペア全件で完全一致しており、再現性は確認済み。
