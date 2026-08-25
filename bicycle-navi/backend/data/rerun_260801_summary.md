# 2026-08-01 基準・全実験再実行サマリ

実行日: 2026-08-24（JST）  
基準コミット: `main` / `95eaf4c`  
本追記で、Overpass取得時点固定と失敗閉鎖のコード変更を実施

## 1. 結論

GraphHopper 11.0 と `kanto-260801.osm.pbf` を使い、指定された全実験を再実行した。
初回は判定タグだけ8/24 liveだったため、その後コードを修正し、R2・マージン・FNを
**`2026-08-01T20:21:21Z`のOverpass attic dataで再実行した。**

- GraphHopper `datareader.data.date`: **`2026-08-01T20:21:21Z`**
- R2 system側: 15/15ペア成功、全件 `using_edge_ids=true`
- R2 Google側: 15/15 polylineを `score_external_route` で再採点
- マージン分布: **188/379点 = 49.6%**（旧集計と同じ）
- FN抽出: 15/15ペア成功、タグ欠落0。要人手確認候補は **0件**
- ground truthテンプレート: 15/15ラベル、17行を再生成
- 現地確認済み18検出点（16 unique way）: **16 wayすべて生存、6/4→8/1のway version・対象タグ・ノード列の変更0件**
- `known_violations.py`、`RESEARCH.md`、判定基準は変更していない
- 8/1 attic固定版でもR2・マージン・FNの集計値は初回8/24 liveタグ版と同じ

## 2. 実行環境

| 項目 | 値 |
|---|---|
| Dockerイメージ | `israelhikingmap/graphhopper:11.0` |
| PBF | `graphhopper/kanto-260801.osm.pbf` |
| `datareader.data.date` | `2026-08-01T20:21:21Z` |
| `datareader.import.date` | `2026-08-18T04:49:25Z` |
| GraphHopper `/info` | `version=11.0`, `data_date=2026-08-01T20:21:21Z` |
| `known_violations.py` hash | `bd77624b1738145e6cd330ede075665bb8ccdd51`（実行前後で不変） |
| `ground_truth.csv` | 存在せず（作成・上書きなし） |

### 初回再実行時の再現性上の注意（現在は解消済み）

GraphHopperの経路探索データは8/1 PBFに固定されている。一方、既存コードの
`services/overpass.py` は日付指定なしで公開Overpass APIを呼ぶため、
**タグ・geometryの取得時点は実行日（2026-08-24）のlive OSM**である。
コードロジックを変更しないという今回の制約に従い、この挙動は変更していない。
したがって、第3〜第6節の初回記録は「経路グラフ=8/1、判定用Overpass取得=8/24」
として生成された。その後の時点固定修正と再実行結果は第9節に記録する。

現地確認済み16 wayについては、公式OSM way historyと
`[date:"2026-08-01T20:21:21Z"]` のOverpass履歴照会を別途行い、
6/4→8/1に変更がないことを確認した（第6節）。

## 3. R2: 15ペアの再採点

`od_pairs.csv` の15ペアを `analyze_route(v3)` で実行し、
`google_comparison.csv` の system 4列を更新した。
Google polyline 15本も `score_external_route(sample_interval_m=40.0)` で再採点した。

| label | system距離(m) | system時間(s) | system違反 / high | Google採点距離(m) | Google oneway / two-step / 合計 |
|---|---:|---:|---:|---:|---:|
| 渋谷→新宿 | 4439.5 | 1176.3 | 0 / 0 | 3591.2 | 1 / 0 / 1 |
| 東京→渋谷 | 8242.5 | 1969.2 | 3 / 3 | 7261.1 | 0 / 1 / 1 |
| 新宿→池袋 | 5331.5 | 1392.0 | 0 / 0 | 4586.0 | 1 / 0 / 1 |
| 品川→東京 | 7685.9 | 1996.3 | 1 / 1 | 6869.6 | 1 / 0 / 1 |
| 渋谷→六本木 | 3409.2 | 832.1 | 0 / 0 | 2862.6 | 0 / 0 / 0 |
| 下北沢→三軒茶屋 | 2233.1 | 485.2 | 1 / 1 | 2240.5 | 0 / 0 / 0 |
| 高円寺→中野 | 1903.3 | 462.0 | 1 / 1 | 1442.9 | 0 / 0 / 0 |
| 荻窪→阿佐ヶ谷 | 1428.8 | 318.1 | 0 / 0 | 1457.8 | 1 / 0 / 1 |
| 自由が丘→等々力 | 2105.7 | 421.9 | 0 / 0 | 2171.1 | 1 / 0 / 1 |
| 浦和→さいたま新都心 | 4427.7 | 902.1 | 1 / 1 | 4627.3 | 0 / 0 / 0 |
| 吉祥寺→三鷹 | 3988.6 | 932.5 | 1 / 1 | 1688.9 | 0 / 0 / 0 |
| 立川→国分寺 | 6758.4 | 1470.1 | 0 / 0 | 7103.5 | 2 / 0 / 2 |
| 横浜→みなとみらい | 2697.6 | 868.5 | 1 / 1 | 1403.9 | 2 / 0 / 2 |
| 川崎→武蔵小杉 | 8049.3 | 1780.7 | 1 / 1 | 6532.8 | 1 / 0 / 1 |
| 千葉→幕張本郷 | 8416.1 | 1780.5 | 1 / 1 | 12134.9 | 2 / 1 / 3 |
| **合計** | | | **11 / 11** | | **12 / 2 / 14** |

system側でリルートしたのは横浜→みなとみらいの1ペアのみ。
初期距離2277.7mから2697.6mへ **+419.9m** だった。

### 旧記録からの変化

| label | system距離(m) 旧→新 | system違反 旧→新 | Google oneway 旧→新 | Google合計 旧→新 |
|---|---:|---:|---:|---:|
| 渋谷→新宿 | 4175.0 → 4439.5 | 1 → 0 | 1 → 1 | 1 → 1 |
| 東京→渋谷 | 7736.4 → 8242.5 | 3 → 3 | 2 → 0 | 3 → 1 |
| 新宿→池袋 | 5330.8 → 5331.5 | 0 → 0 | 0 → 1 | 0 → 1 |
| 品川→東京 | 7801.3 → 7685.9 | 1 → 1 | 3 → 1 | 3 → 1 |
| 渋谷→六本木 | 3409.2 → 3409.2 | 0 → 0 | 0 → 0 | 0 → 0 |
| 下北沢→三軒茶屋 | 2207.6 → 2233.1 | 1 → 1 | 0 → 0 | 0 → 0 |
| 高円寺→中野 | 1891.8 → 1903.3 | 0 → 1 | 0 → 0 | 0 → 0 |
| 荻窪→阿佐ヶ谷 | 1425.2 → 1428.8 | 0 → 0 | 1 → 1 | 1 → 1 |
| 自由が丘→等々力 | 1975.7 → 2105.7 | 0 → 0 | 1 → 1 | 1 → 1 |
| 浦和→さいたま新都心 | 4373.3 → 4427.7 | 1 → 1 | 1 → 0 | 1 → 0 |
| 吉祥寺→三鷹 | 3959.0 → 3988.6 | 1 → 1 | 0 → 0 | 0 → 0 |
| 立川→国分寺 | 6747.8 → 6758.4 | 0 → 0 | 2 → 2 | 2 → 2 |
| 横浜→みなとみらい | 2693.6 → 2697.6 | 1 → 1 | 2 → 2 | 2 → 2 |
| 川崎→武蔵小杉 | 8049.4 → 8049.3 | 1 → 1 | 1 → 1 | 1 → 1 |
| 千葉→幕張本郷 | 8338.3 → 8416.1 | 0 → 1 | 4 → 2 | 5 → 3 |
| **合計** | | **10 → 11** | **18 → 12** | **20 → 14** |

主な変化は、system側では渋谷→新宿の違反消滅、高円寺→中野と千葉→幕張本郷の
違反出現。Google側ではoneway検出が18→12に減少した。

## 4. マージン分布と曖昧率

生成物: `fix_verification_v2.md`、`verify_match_margin_points.csv`、
`verify_v2_analyze_route.csv`

- 全判定点: **379**
- `match_ambiguous=True`: **188（49.6%）**
- 候補1本のみ: 0
- 候補0本: 0
- 旧集計: 188/379（49.6%）→ **集計値は不変**
- 想定外4地点: 0.51〜1.75m、4/4が2m未満
- 正常群最小: 2.64m
- 最終C節: 15/15ペアで `google_comparison.csv` と一致し、全件 `edge_id=true`

公開Overpassの連続利用制限を避けるためA・Bは既存CLIの `--label` でペア別に実行し、
成功した379行を結合した。C節は全件 `using_edge_ids=true` を確認したAPI結果を使い、
スクリプト既存の `build_markdown` で最終報告書を生成した。

## 5. FN候補抽出とground truthテンプレート

### FN候補

生成物: `fn_candidates_oneway.md`、`fn_candidates_oneway.csv`

| 項目 | 延べ | unique |
|---|---:|---:|
| 初期ルート通過way | 662 | 617 |
| oneway way | 254 | 238 |
| 同一実行で検出されたoneway way | 1 | 1 |
| 未検出oneway（診断対象） | 253 | 237 |

診断内訳:

- `bicycle_exempt`: 15
- `forward_travel`: 238
- `short_segment`: 0
- `tag_fetch_failed`: 0
- `unknown`: 0
- **要人手確認: 0**

したがって、タグ基準の暫定Recallは1.000。ただし分子の検出1件が真陽性かどうかは
Precision側の人手判定に依存し、OSMにonewayタグ自体がない現実上の見逃しは母集団に入らない。

最終出力は15/15成功、全ペア `using_edge_ids=true`、タグ欠落0を検証済み。

### ground truthテンプレート

生成物: `ground_truth_template.csv`

- 15/15ラベル、17行
- 検出行11、違反なしを表す行6
- way_id取得成功11/11検出行
- way_id fallback 0、Overpassタグ取得失敗0
- `ground_truth.csv` 本体は存在せず、作成・上書きしていない

## 6. 現地確認済み18検出点の追跡

`known_violations.py` は16 unique wayを持つ。way 28413948と22961575が各2地点で
検出されていたため、検出点の合計が18となる。

### 生存・編集履歴

公式OSM way historyから、6/4時点（`2026-06-04T20:21:13Z`以前の最終版）と
8/1時点（`2026-08-01T20:21:21Z`以前の最終版）を比較した。

- 16/16 wayが両時点に存在
- version変更: 0
- `oneway` / `oneway:bicycle` / `cycleway*` / `highway` 変更: 0
- ノード列変更: 0（同一versionのため、削除・分割・geometry変更なし）
- 8/1時点Overpass照会: 16/16件取得成功

つまり、**変化したway_idはなく、変化前後で並べるべきタグ差分は0件**だった。
全wayで `oneway=yes` は維持され、`oneway:bicycle` は空欄のままだった。

| 群 | way_id | version 6/4=8/1 | 対象タグ（両時点同一） | 旧検出点→今回同一way検出 | 今回の状況 |
|---|---:|---:|---|---:|---|
| A | 138533178 | 10 | `oneway=yes; highway=unclassified` | 1→1 | 同じwayで検出継続 |
| A | 23690216 | 10 | `oneway=yes; cycleway:left=no; highway=unclassified` | 1→0 | 現地点はway 172281627（footway）にマッチ |
| A | 142222817 | 4 | `oneway=yes; highway=residential` | 1→1 | 同じwayで検出継続 |
| A | 80835360 | 4 | `oneway=yes; highway=residential` | 1→1 | 同wayを隣接サンプルidx10で検出。旧idx11はway 87704269にマッチしmisaligned |
| A | 350353685 | 5 | `oneway=yes; highway=service` | 1→1 | 同じwayで検出継続 |
| A | 325347768 | 3 | `oneway=yes; highway=unclassified` | 1→0 | 現地点はway 1444864905にマッチしmisaligned（margin 0.51m） |
| B | 22961575 | 28 | `oneway=yes; highway=trunk` | 2→0 | 2点とも分離way 23052404に正しくマッチ |
| B | 741785139 | 3 | `oneway=yes; highway=tertiary_link` | 1→1 | 検出継続（B群として残存） |
| B | 667962675 | 9 | `oneway=yes; highway=trunk` | 1→0 | 分離way 667962674にマッチ |
| B | 474601303 | 7 | `oneway=yes; cycleway:left=no; highway=tertiary` | 1→0 | way 858775693にマッチ（margin 0.88m、曖昧） |
| B | 271979254 | 16 | `oneway=yes; cycleway:left=shared_lane; highway=primary` | 1→0 | 分離way 858775692にマッチ |
| B | 1429406683 | 3 | `oneway=yes; cycleway=lane; highway=tertiary` | 1→1 | 検出継続（B群として残存） |
| B | 263457845 | 7 | `oneway=yes; highway=tertiary` | 1→0 | 分離way 31875063にマッチ |
| C | 28413948 | 7 | `oneway=yes; highway=unclassified` | 2→2 | 真の違反2点を検出継続 |
| C | 853388885 | 4 | `oneway=yes; highway=tertiary` | 1→1 | 真の違反を検出継続 |
| D | 151808609 | 6 | `oneway=yes; highway=unclassified` | 1→1 | 同じwayで検出継続 |

群別まとめ:

- **A群6件:** タグ変更0、`oneway:bicycle=no`等の追加0。4 wayは検出、2 wayは現地点のマッチ先変更で非検出。
- **B群7件:** 5 wayは分離way等へマッチして非検出、741785139と1429406683の2 wayは検出継続。
- **C群2件（3検出点）:** すべて検出継続。真の違反は失われていない。
- **way 138533178:** v10のまま。`oneway=yes`、自転車除外タグなし、検出継続。
- 既知18検出点のうち同一wayで今回検出されたのは10点。Google側oneway全体は12点で、残り2点は既知18点以外の検出。

## 7. 実行時の問題と対処

### 公開Overpassの429・502・504・タイムアウト

連続実行中に複数回発生した。コードは変更せず、既存CLIのラベル指定・間隔指定と
再試行で対応した。最終成果物は候補0件や `edge_id=false` が混じっていないことを検証した。

### `score_google_routes.py --all --write` の保護範囲

立川→国分寺で全Overpass先が失敗すると、同一async taskの回路遮断により後続の
横浜・川崎・千葉も0件になった。しかし渋谷→新宿の回帰確認は先に成功していたため、
スクリプトは誤った0をCSVへ書き込んだ。4ペアを別プロセスで個別再採点し、
有効結果（立川2、横浜2、川崎1、千葉oneway2+two-step1）に修復した。

これは今回見つかった追加問題である。現状の回帰ガードは「渋谷→新宿が成功した」ことしか
保証せず、途中・後半ペアのOverpass失敗による偽0を防げない。今回はコード変更禁止のため
修正していない。

### `verify_match_margin.py` / `extract_fn_candidates.py`

一括実行では回路遮断後の結果が混ざるため、最終出力はペア単位の品質条件
（候補0なし、`using_edge_ids=true`、タグ欠落0）を満たした実行だけを採用した。

## 8. 再生成したファイル

- `backend/data/google_comparison.csv`
- `backend/data/fix_verification_v2.md`
- `backend/data/verify_match_margin_points.csv`
- `backend/data/verify_v2_analyze_route.csv`
- `backend/data/fn_candidates_oneway.md`
- `backend/data/fn_candidates_oneway.csv`
- `backend/data/ground_truth_template.csv`
- `backend/data/rerun_260801_summary.md`（本ファイル）

この初回再実行時点ではPythonコードを変更していなかった。その後、`RESEARCH.md`、
`known_violations.py`、`ground_truth.csv`を保持したまま、取得層と実行スクリプトを
第9節のとおり修正した。

## 9. Overpass時点固定・偽0防止修正後の再実行（2026-08-24追記）

### 9.1 修正内容

`services/overpass.py` の通信失敗と「正常に検索したが該当wayが0件」を分離した。
2つのendpointがともに失敗した場合は `OverpassUnavailableError` を送出し、空タグや
違反0件として処理を続けない。

- `score_google_routes.py`: ペアごとに成功／ERRORを保持し、成功N件・失敗M件を表示する。
  1件でも失敗した実行では `google_comparison.csv`を1行も更新せず、非ゼロ終了する。
- `route_analyzer.py`: by-ID取得・点ベース取得の失敗をroad classだけで補完せず、
  呼び出し側へ伝播する。指定wayの一部が欠けた場合も失敗扱いにする。
- `verify_match_margin.py`: ペア単位で失敗を集計し、部分結果を成果物へ書き込まない。
- `extract_fn_candidates.py`: タグ取得失敗・way欠落をFN 0件として扱わず、
  部分結果を成果物へ書き込まない。
- `prepare_ground_truth.py`: 経路またはタグ取得が一部失敗した場合、既存テンプレートを
  上書きせず非ゼロ終了するよう同種の経路を修正した。

各ネットワーク試行はペアごとに独立したContextで実行する。1ペアで回路遮断しても
後続ペアへ状態が漏れない。ペア単位の再試行は最大2回で、2回とも失敗した場合だけ
最終ERRORとする。

### 9.2 Overpassの取得時点とendpoint

実験設定は `backend/data/experiment_settings.json` に置いた。

```json
{
  "graphhopper_data_date": "2026-08-01T20:21:21Z",
  "overpass_snapshot_date": "2026-08-01T20:21:21Z"
}
```

実験スクリプトは両日時が一致することを起動時に検証し、Overpassクエリへ
`[date:"2026-08-01T20:21:21Z"]`を追加する。FastAPIの通常起動はこの設定を
自動適用しないため、環境変数 `OVERPASS_SNAPSHOT_DATE` が未指定ならlive OSMを使う。

事前の単一way実動確認（way `138533178`）:

| endpoint | 結果 | 所要時間 | 返却 |
|---|---|---:|---|
| `overpass-api.de` | 成功 | 10.34秒 | way 138533178 / version 10 |
| `maps.mail.ru` | 成功 | 18.58秒 | way 138533178 / version 10 |
| `overpass.kumi.systems` | 確認不能 | 0.8〜115.7秒 | HTTP 500/502（liveも失敗） |
| `overpass.private.coffee` | 確認不能 | 40.48秒 | HTTP 500 |

このため、確認済みの前2系統だけを使用する。成功した取得結果には使用endpointを
記録し、フォールバック元の失敗試行も実行サマリへ残す。

### 9.3 修正後R2

実行時のGraphHopper `/info`:

- `version=11.0`
- `data_date=2026-08-01T20:21:21Z`
- `import_date=2026-08-18T04:49:25Z`

system側は15/15ペア成功・全件 `using_edge_ids=true`。第3節の15行と距離、時間、
違反数、高信頼違反数が全件一致した。合計は **11件 / high confidence 11件**。

Google側は最初の全件実行で下北沢→三軒茶屋が両系統504となった。この実行は
**成功14件・失敗1件、終了コード非ゼロ、`google_comparison.csv`更新0行**となり、
偽0防止を実通信でも確認できた。待機・ペア再試行を有効にした再実行は15/15成功し、
CSVを上書きした。合計は **oneway 12 / two-step 2 / 計14件**。

成功した最終Google実行のendpoint集計:

| endpoint | 判定対象 | 成功クエリ | 失敗試行 |
|---|---:|---:|---:|
| `overpass-api.de` | 276 | 13 | 11 |
| `maps.mail.ru` | 126 | 9 | 2 |

**8/24 liveタグ版からの差は、system・Googleとも15ペア全列で0件だった。**
タグ取得時点を8/1へ揃えても、今回のR2数値そのものは変わらなかった。

### 9.4 マージン分布とFN

マージン検証はA・B 15/15、`analyze_route(v3)` 15/15が成功した。

- 判定点: 379
- `match_ambiguous=True`: 188（49.6%）
- 8/24 liveタグ版との差: 0点、0.0ポイント

マージン＋system再確認のendpoint集計:

| endpoint | 判定対象 | 成功クエリ | 失敗試行 |
|---|---:|---:|---:|
| `overpass-api.de` | 537 | 16 | 15 |
| `maps.mail.ru` | 504 | 14 | 1 |

FN抽出も15/15成功した。

- 通過way: 延べ662 / unique 617
- oneway way: 254
- FN候補: 253（`bicycle_exempt=15`, `forward_travel=238`）
- `tag_fetch_failed=0`, `unknown=0`, 要人手確認0
- 8/24 liveタグ版との差: 全集計0

FN実行のendpoint集計（最初のby-ID取得とLRUキャッシュ再利用の判定対象を含む）:

| endpoint | 判定対象 | 成功クエリ | 失敗試行 |
|---|---:|---:|---:|
| `overpass-api.de` | 778 | 10 | 7 |
| `maps.mail.ru` | 546 | 5 | 2 |

### 9.5 バルク取得とキャッシュ

379判定点を1点ずつ問い合わせる実装ではない。

- Google／マージン: 1ルートの判定点をUnionクエリ1回で取得する。
  右折候補があるGoogleルートだけ追加の一括クエリを使う。
- system／FN: 1ルートの全way IDを1クエリで取得する。
- FN Step 2: Step 1で取得したwayタグをLRUキャッシュから再利用する。
- LRUキーを `(snapshot_date, way_id)` とし、8/1タグとliveタグを混在させない。

attic単一wayの事前テストは10〜19秒、実ルートの一括クエリでは429/504も発生した。
そのため15ペアは数分単位になるが、判定点数に比例して379回通信する時間にはならない。

### 9.6 過去の偽0混入検証

修正前のCSVには、行ごとの取得endpoint、通信成功状態、snapshot日時が保存されていなかった。
したがって、**過去の任意の0が「正常取得後の0」か「通信失敗による偽0」かをCSVだけから
確定することは不能**である。連続した後半0件などを疑うことはできるが、証明にはならない。

現在の `google_comparison.csv` は、次の条件で検証した。

1. 8/1 attic固定で15本を全件採点。
2. 15/15成功・失敗0を確認。
3. endpoint別の判定対象数と失敗試行数を記録。
4. 成功した15結果だけを一括書き込み。
5. system側も別の `analyze_route(v3)` 全件成功結果と4列を照合し、15/15一致。

このため、**現在のR2の0件を含む各値は、少なくとも修正版コード・8/1 atticタグ・
今回の全件成功実行に対して正しい。** 7月以前の未記録データまで遡る保証はできない。

### 9.7 テストと保護対象

- 新規障害回帰テスト: 6/6成功
  - 日時注入
  - 両系統失敗時の例外
  - フォールバック先／件数の記録
  - 偽0結果を生成せずERROR化
- 既存matchスモークテスト: 50/50成功
- 既存FNスモークテスト: 65/65成功
- `known_violations.py`: git blob `bd77624b1738145e6cd330ede075665bb8ccdd51`、変更なし
- `RESEARCH.md`: 変更なし
- `check_oneway_violation` / `check_two_step_turn`: 判定基準変更なし
- 最近傍way選択への方向情報追加なし、非車道除外フィルタ追加なし

今回追加・更新した主なファイル:

- `backend/data/experiment_settings.json`
- `backend/data/google_comparison.csv`
- `backend/data/score_google_routes_result.csv`
- `backend/data/fix_verification_v2.md`
- `backend/data/verify_match_margin_points.csv`
- `backend/data/verify_v2_analyze_route.csv`
- `backend/data/fn_candidates_oneway.md`
- `backend/data/fn_candidates_oneway.csv`
- `backend/data/rerun_260801_summary.md`
- `docs/DATA_PROVENANCE.md`
