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
