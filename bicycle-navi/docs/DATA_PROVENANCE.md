# データ来歴（Data Provenance）

実験の再現性を担保するため、ルーティングエンジンと入力 OSM データの**固定した版**をここに記録する。
更新した場合は行を追加し、`RESEARCH.md` の基準時点も併せて更新すること（削除・上書きはしない）。

---

## 現行（2026-08-18 〜）

### OSM データ

| 項目 | 値 |
|---|---|
| ファイル名 | `bicycle-navi/graphhopper/kanto-260801.osm.pbf` |
| 取得元 | https://download.geofabrik.de/asia/japan/kanto-260801.osm.pbf |
| スナップショット日 | 2026-08-01（Last-Modified: Sat, 01 Aug 2026 23:10:03 GMT） |
| 取得日時 | 2026-08-18 13:47（JST） |
| サイズ | 481,636,841 バイト |
| MD5 | `7ff182dbcdbe9ba6928f618b300f58a3` |
| SHA-256 | `a14ec3138bc9bcde57e778746d7121d9b1369da36a7be123662ec215f818e7af` |
| MD5 照合 | Geofabrik 公開の `kanto-260801.osm.pbf.md5` と**一致を確認済み** |
| ライセンス | ODbL（© OpenStreetMap contributors） |

### ルーティングエンジン

| 項目 | 値 |
|---|---|
| Docker イメージ | `israelhikingmap/graphhopper:11.0` |
| GraphHopper バージョン | 11.0（リリースコミット `69e50f6e`, 2025-10-14） |
| イメージ digest | `sha256:e77e14e48ea69ea7bb0eb71ddc9d583e5ce85dd295475572371f72ed4880a1ff` |
| グラフ格納形式 | geometry=7, node=9, edge=24, location_index=5, EM=4 |

digest の確認：

```bash
docker inspect israelhikingmap/graphhopper:11.0 --format '{{range .RepoDigests}}{{.}}{{end}}'
```

### 構築されたグラフ

| 項目 | 値 |
|---|---|
| `datareader.data.date` | `2026-08-01T20:21:21Z` |
| `datareader.import.date` | `2026-08-18T04:49:25Z`（JST 13:49） |
| グラフ出力先 | `bicycle-navi/graphhopper/default-gh/` |
| ビルド所要時間 | 約 52 秒（CH/LM の事前計算を行わない設定のため） |
| edges / nodes | 5,151,885 / 3,795,369 |
| `/info` の応答 | `version: 11.0`, `data_date: 2026-08-01T20:21:21Z` |

### 2026-08-24 全実験再実行

8/1基準グラフへの固定後、R2、マージン分布、FN候補抽出を
再実行した。2026-08-28の正は `../../docs/測定結果_凍結版.md`、削減前の中間出力は
tag `measurement-freeze-20260828` を参照する。

| 再生成ファイル | 内容 |
|---|---|
| `backend/data/measurement_freeze_20260828.csv` | 8指標の機械可読な正 |
| `backend/data/measurement_freeze_od_20260828.csv` | R1入力座標とR2 polyline端点 |
| `backend/data/route_display_distances_freeze_20260828.csv` | Google Maps表示距離と本システム返却距離。Google値はcommit `cb14177`から抽出 |

再実行時の確認値:

- `datareader.data.date=2026-08-01T20:21:21Z`
- R2 system違反11件、Google採点14件（oneway 12 / two-step 2）
- 曖昧率188/379 = 49.6%
- FN診断対象253件（自転車除外15、順走238、要人手確認0）
- 現地確認済み18検出点（16 unique way）は6/4→8/1でway version・対象タグ・ノード列の変更0件

**初回再実行時の注意（現在は解消済み）:** GraphHopperの経路グラフは8/1 PBFに固定されているが、当時のコードの
Overpass取得には日付指定がない。この再実行の判定用タグ・geometryは実行日
（2026-08-24）のlive OSMから取得した。既知16 wayのみ、公式OSM履歴と8/1時点の
Overpass履歴照会を追加実施し、6/4→8/1に変更がないことを確認した。

### 2026-08-24 判定用Overpassの時点固定修正

上記の「経路グラフは8/1、判定タグは実行日のlive」という時点差を解消した。
実験用設定は [`../backend/data/experiment_settings.json`](../backend/data/experiment_settings.json)
に置き、次の2値を同一にする。値が異なる場合、実験スクリプトは開始時に失敗する。

| 設定 | 現在値 |
|---|---|
| `graphhopper_data_date` | `2026-08-01T20:21:21Z` |
| `overpass_snapshot_date` | `2026-08-01T20:21:21Z` |

取得モードは次の2つである。

- **実験モード:** `score_google_routes.py`、`verify_match_margin.py`、
  `extract_fn_candidates.py` が上記設定を読み、クエリに
  `[date:"2026-08-01T20:21:21Z"]` を付ける。
- **実運用モード:** FastAPI通常起動では実験設定を読み込まない。
  環境変数 `OVERPASS_SNAPSHOT_DATE` が未指定ならlive OSMを参照する。
  必要な場合だけ同環境変数で任意の履歴日時へ固定できる。

#### 使用エンドポイント

8/1時点のway `138533178`を使った事前確認結果に基づき、次の2系統だけを使用する。

| endpoint | 8/1 attic実動確認 | 単一wayテスト所要時間 |
|---|---|---:|
| `https://overpass-api.de/api/interpreter` | 成功 | 約10.3秒 |
| `https://maps.mail.ru/osm/tools/overpass/api/interpreter` | 成功 | 約18.6秒 |

旧3系統目の `overpass.kumi.systems` は通常・atticともHTTP 500/502となり、
後継と案内されている `overpass.private.coffee` もatticテストがHTTP 500だった。
実動確認できない系統を増やすより、確認済み2系統へ限定する方針とした。

取得結果には使用endpointを記録し、各実験の標準出力・明細・再実行報告に
endpoint別の判定対象数、成功クエリ数、失敗試行数を残す。両系統が失敗した場合は
`OverpassUnavailableError` としてペア全体を失敗させ、タグ空欄や違反0件として継続しない。
一部失敗時は成果物を上書きせず、成功件数・失敗件数を表示して非ゼロ終了する。

速度対策として、点ベース採点は1ルートの全判定点をUnionクエリで一括取得し、
edge_idベース採点は1ルートの全way IDを1クエリで取得する。wayタグのLRUキャッシュは
`(取得日時, way_id)` をキーとし、liveと履歴タグの混在を防ぎながらFN抽出の再取得を省く。
379判定点を379回逐次問い合わせる実装ではない。

修正後、R2、マージン分布、FN候補抽出を8/1 atticタグで再実行した。

- R2 system: 15/15成功、違反11件
- R2 Google: 15/15成功、違反14件（oneway 12 / two-step 2）
- マージン: 188/379点 = 49.6%
- FN候補: 253件（タグ欠落0、要人手確認0）
- 8/24 liveタグ版からの数値差: なし

凍結した結果と旧値との差分は`../../docs/測定結果_凍結版.md`を参照する。削減した再実行明細が必要な場合はtag `measurement-freeze-20260828`から取得する。

---

## 履歴

### 旧構成（〜2026-08-18・廃止）

| 項目 | 値 |
|---|---|
| Docker イメージ | `israelhikingmap/graphhopper:latest`（**固定されていなかった**） |
| OSM データ | `--url .../kanto-latest.osm.pbf`（**起動のたびに最新版を再取得**） |
| 構築グラフ | `datareader.data.date=2026-06-04T20:21:13Z` / `import.date=2026-06-05T03:02:19Z` |
| グラフ格納形式 | geometry=**8**, node=9, edge=24, location_index=5, EM=4 |
| 旧グラフのメタデータ | `bicycle-navi/graphhopper/properties-260604.txt.bak` に退避 |
| 同上（git 管理下の複製） | [`graph-properties-260604.txt`](graph-properties-260604.txt)（`.bak` は `.gitignore` の `*.bak` に該当し git に入らないため、記録用に複製） |

**廃止した理由：**

1. `:latest` は GraphHopper の master ブランチから随時ビルドされるスナップショットで、リリース版ではない。
   2026-06-18 の自動更新で geometry version が 8→9 に変わり、6月構築のグラフが
   `Unexpected version for 'geometry'. Got: 8, expected: 9` で読めなくなった。
2. `--url` は起動のたびに Geofabrik の最新版を再取得するため、実験が依存する
   OSM スナップショットが意図しないタイミングで変わりうる。
   さらに保存先がコンテナ内 `/graphhopper/data.pbf` だったため、ホストに残らなかった。

**注意：** 旧グラフの geometry version 8 は master の中間状態（2026-01-05〜2026-05-19）にのみ存在し、
**リリース版には一度も含まれていない**（11.0 は geometry=7、現在の master は geometry=9）。
そのため旧グラフはどの公開タグでも読めず、再構築以外の選択肢がなかった。

また 2026-06-04 の OSM スナップショットは Geofabrik の保持期間
（直近約1週間の日次 + 直近約3か月の月初）を過ぎており、**再取得不可能**である。
これは `RESEARCH.md` に記した「pbf ファイル自体を保存しておくべきだった」という教訓が
現実化したものであり、今後は本ファイルと実 pbf の保存で再発を防ぐ。
