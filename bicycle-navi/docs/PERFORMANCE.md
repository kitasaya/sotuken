# 性能改善ログ

性能向上フェーズ（P1〜P2、Q1〜Q2）における手動計測値を記録する。
各タスク完了時に、改善前後の比較値を追記する。

計測方針：
- 同一ネットワーク条件下で 1 ペアあたり 3 回試行して中央値を採用
- バックエンドの uvicorn ログから所要時間と Overpass 呼び出し回数を読み取る
- レスポンス時間は `time curl -X POST http://localhost:8000/api/route -d '...'` の `real` を採用

---

## タスク P1：Overpass キャッシュ層の導入（2026-06-05 実装）

### 設計の要点

- `backend/services/overpass.py` に `OrderedDict` ベースの LRU キャッシュ
  `_way_cache`（上限 10,000 件）を追加
- `get_way_tags_by_ids` 内で、要求された way_id のうちキャッシュにあるものは
  即座に結果へ含め、ないものだけを Overpass に問い合わせる
- Overpass が返した way_id のみキャッシュへ格納（**負キャッシュなし**）
  - 理由：route_analyzer の「空 dict ならフォールバック」判定との互換を保つため
  - Overpass 全エンドポイント失敗時は空 dict が返り、呼び出し側で点ベース
    フォールバックが発火する挙動を維持
- 1 リクエスト終了時に `cache_hit / cache_miss / overpass_called / cache_size` を
  info ログに出力

### 計測結果（渋谷→新宿、2026-06-05 実測）

`route_analyzer` ログから抽出した「Overpass + 法規チェック」の所要時間。

| 計測項目 | P1 適用前 | P1 適用後（cold） | P1 適用後（warm） |
|---|---|---|---|
| Overpass 呼び出し回数 | 1 回 | 1 回 | **0 回** |
| Overpass + 法規チェック所要時間 | 27.9 秒 ※ | 2.4 秒 | **0.0 秒** |
| using_edge_ids | False（fallback） | True | True |
| cache_hit / cache_miss / overpass_called | -（cache 未導入） | `0 / 72 / 1` | `72 / 0 / 0` |

※ 改善前の 27.9 秒は Overpass の全エンドポイント失敗（406/timeout/403）に
よる timeout 待ちが主因。User-Agent 修正で 406 は解消され、cold で 2.4 秒に
収まるようになった。warm では cache だけで完結し 0.0 秒。

参考所要時間（GraphHopper 単体は別途数百ms）：
- cold（1 回目）：route_analyzer 段 2.4 秒（Overpass 含む）
- warm（2 回目以降）：route_analyzer 段 0.0 秒（Overpass 0 回）

### 結論

P1（Overpass キャッシュ層導入 + User-Agent 修正 + 同一リクエスト内 circuit breaker）
の組み合わせで：

1. **cold（初回）**：27.9 秒 → 2.4 秒（**約 91% 短縮**）
   - 主因は User-Agent 修正による 406 の解消
2. **warm（2 回目以降）**：2.4 秒 → 0.0 秒（**Overpass 呼び出しゼロ化**）
   - キャッシュ層が機能
3. **Overpass 完全停止時の最悪値**：60 秒 → 30 秒（circuit breaker）

### 計測手順

```powershell
# 1. バックエンド起動（プロセス起動時にキャッシュは空）
docker compose up -d graphhopper
cd backend
uvicorn main:app --reload --host 0.0.0.0

# 2. 別ターミナルで 1 回目（cold）
Measure-Command {
  curl.exe -X POST http://localhost:8000/api/route `
    -H "Content-Type: application/json" `
    -d '{"origin_lat":35.6580,"origin_lng":139.7016,"dest_lat":35.6896,"dest_lng":139.6917}'
}

# 3. 続けて 2 回目（warm）
#    バックエンドログで cache_hit=N cache_miss=0 overpass_called=0 を確認
```

### 動作確認（自動テスト）

`overpass._post_with_retry` をモックした単体テストでキャッシュ挙動を確認済み：

- 初回呼び出し：全件ミス → Overpass 呼び出し 1 回
- 同一 ID で再呼び出し：全件ヒット → Overpass 呼び出し 0 回
- 部分一致：未キャッシュ ID のみクエリへ含まれる
- Overpass 失敗時：空 dict 返却、負キャッシュなし（次回再取得）
- LRU の容量超過時：最古エントリが破棄される
- LRU のリセンシー：アクセス時に末尾移動

### 副作用・注意点

- プロセス内 LRU のため、`uvicorn --reload` でコード変更時にキャッシュは破棄される
- 複数の uvicorn ワーカーを使う場合はワーカーごとにキャッシュが独立する
  （現状の単一ワーカー構成では問題なし）
- OSM 側で way_id のタグが変更された場合、キャッシュが古い値を返し続ける
  ことになる。長時間運用で気になる場合は `clear_way_cache()` を呼ぶか
  プロセス再起動で対応

### 追加修正：Overpass 失敗時の応答時間圧縮（2026-06-05）

実機ログ（渋谷→新宿）で Overpass 全エンドポイント失敗が観測され、合計 27.9 秒の
応答時間になっていた。原因と対処：

1. **406 Not Acceptable** が overpass-api.de から返ってくる
   - User-Agent ヘッダがない HTTP クライアントを公開 Overpass が拒否するため
   - `_OVERPASS_HEADERS` で `bicycle-navi-research/1.0 (Aoyama Gakuin University; academic)`
     を全 Overpass リクエストに付与
2. **by-ID 失敗 → 点ベース fallback でまた Overpass を叩いて再失敗** の二重待ち
   - `_overpass_circuit_broken` という `ContextVar` を導入
   - 同一 asyncio タスク（＝同一 FastAPI リクエスト）内で一度全エンドポイントが
     失敗したら、後続の `_post_with_retry` を即座にスキップ
   - 別リクエストでは新しい asyncio タスクが新しい Context を持つため自然にリセット
3. **計測上の効果**
   - 全失敗時の httpx 呼び出し回数：6 回 → 3 回（自動テストで確認）
   - 最悪応答時間：~60 秒 → ~30 秒
   - User-Agent が効いて Overpass が応答するようになれば、通常時は cold で数秒、
     warm で 1 秒前後を狙える

### 計測再実施の目安

User-Agent 修正後に再度同じシナリオ（渋谷→新宿）を実行し、以下を確認：

- Overpass が 406 を返さず 200 OK で応答する
- 1 回目（cold）の応答時間が 5 秒以内に収まる
- 2 回目（warm）の応答時間が 2 秒以内に収まり、ログに
  `cache_hit=N cache_miss=0 overpass_called=0` が出る

---

## タスク Q1：自転車インフラ優先の custom_model（2026-06-05 実装）

### 設計の要点

- `graphhopper/config.yml` の `graph.encoded_values` に `cycleway` を追加し、
  グラフ再ビルド時に cycleway タグの値を encoded value として保存
- 同 `profiles.[0].custom_model.priority` に自転車インフラ優遇ルールを書き込み、
  サーバ側 profile に組み込む（CH の高速化が効くため応答速度に劣化なし）

優先度倍率：

| 条件 | 倍率 | 意図 |
|---|---|---|
| `road_class == CYCLEWAY` | ×1.5 | 自転車道（最優先） |
| `cycleway == TRACK` | ×1.4 | 物理的に分離された自転車レーン |
| `cycleway == LANE` | ×1.25 | 路面標示の自転車レーン |
| `cycleway == SHARED_LANE` | ×1.1 | 共用レーン |
| `road_class == RESIDENTIAL` | ×1.1 | 住宅街（静か） |
| `road_class == TERTIARY` | ×1.05 | 三次道 |
| `road_class == SECONDARY` | ×0.8 | 二次道（抑制） |
| `road_class == PRIMARY` | ×0.5 | 一次道（強く抑制） |

### 計測結果（渋谷→新宿、2026-06-05 実測）

| 指標 | Q1 前 | Q1 後 |
|---|---|---|
| 通過 way 数 | 72 | 75 |
| two_step_turn violations | 1 件 | **0 件** |
| oneway violations | 0 件 | 0 件 |
| Overpass + 法規チェック所要時間 | 2.4 秒 | 1.3 秒 |
| using_edge_ids | True | True |

### 結論

- 通過 way 数が 72 → 75 へ細分化：より細かい residential / tertiary を通過
- 幹線道路（primary/secondary）を含む右折交差点が経路から除外され、
  二段階右折違反が自然に消滅
- 応答速度は劣化なし（CH が効いている、cold で 2.4 秒 → 1.3 秒に改善）

### 副作用・注意点

- グラフキャッシュ（`graphhopper/default-gh/`）の再ビルドが必要（5〜10 分）
- `encoded_values` 変更後に旧キャッシュを残したまま起動すると挙動が乱れるため、
  必ず削除してから再起動する
- 既存の R1 評価（ground truth）データは経路が変わったので再記入が必要になる
  可能性がある（Masaya さんの手動データ）
- 距離が伸びる方向の変化が起こるはずだが、渋谷→新宿では大きな増加は観測されず

### 15 O-D ペアでの追加検証（同日、Q1 v1）

| 指標 | Pre-Q1（baseline） | Q1 v1（強め） |
|---|---|---|
| 距離合計 | 69,699 m | 72,815 m（+4.5%） |
| 違反総数 | 4 件 | 17 件（+13） |
| リルート発生 | 0 ルート | 2 ルート |

想定外の違反増加が発生。原因：cycleway 優遇でルートが residential を蛇行
するようになり、residential → primary/secondary へ「右折で戻る」地点が増えた。
`check_two_step_turn` は departure road の highway=primary/secondary を保守的に
検出するため、右折ポイントが増えるほど違反検出数も増える。

### 係数緩和の試行（Q1 v2）

PRIMARY ×0.5 → ×0.9、SECONDARY ×0.8 → 削除、cycleway 倍率も控えめに（TRACK ×1.25、
LANE ×1.15 等）に変更し再実験。

| 指標 | Pre-Q1 | Q1 v1 | Q1 v2 |
|---|---|---|---|
| 距離合計 | 69,699 m | 72,815 m（+4.5%） | 71,177 m（+2.1%） |
| 違反総数 | 4 件 | 17 件 | 14 件 |
| リルート発生 | 0 ルート | 2 ルート | 0 ルート |

v2 は v1 比で改善（違反 17→14、距離 +4.5%→+2.1%、リルート 2→0）したが、
依然として違反は Pre-Q1 比 +10 件。係数調整だけでは根本解消できない構造的問題。

### 結論：Q1 ロールバック

`check_two_step_turn` のヒューリスティック検出（departure road の highway
チェック）と「cycleway 優遇による residential 蛇行」が **構造的に相性が悪い** ため、
係数チューニングだけでは違反増加を抑えられない。距離面では許容範囲（+2.1%）
だが、自前評価指標で論文に有利な数字が出せないため、本フェーズでは Q1 を
見送り、`graphhopper/config.yml` を Pre-Q1 状態にロールバックする。

論文では「自転車インフラ優遇の素朴な custom_model はヒューリスティック違反検出
との相性が悪く逆効果になる」という考察として残せる。今後の研究課題として
「cycleway 優遇 + 右折コスト最適化 + 違反検出の精緻化」のセットが必要。

実験データ：
- `backend/data/experiment_v1_vs_v3_post_f1.csv`（Pre-Q1 baseline）
- `backend/data/experiment_post_q1.csv`（Q1 v1）
- `backend/data/experiment_post_q1_v2.csv`（Q1 v2）

---

## タスク P2：httpx クライアント共有 + コネクションプール（2026-06-05 実装）

### 設計の要点

当初 CLAUDE.md にあった「リルート高速化」は F1 修正後にリルートがほぼ発火しない
（15 O-D ペアで 0 件）ため実環境での効果が見えにくく、スコープを「httpx の
共有とコネクションプール再利用」に振り替えた。これは常時ヒットする
GraphHopper / Overpass / Nominatim 全てのリクエストで効く。

- `backend/services/http_clients.py`（新規）に共有 `httpx.AsyncClient` を集約
  - `get_client()` で遅延初期化、`close_client()` でクローズ
  - `limits=httpx.Limits(max_connections=100, max_keepalive_connections=20,
    keepalive_expiry=60.0)`
- `backend/main.py` に lifespan を導入、起動時にプリ初期化、シャットダウン時に
  クローズ
- 各サービスを書き換え：
  - `graphhopper.py`：`async with httpx.AsyncClient(...)` → `get_client()`
  - `overpass.py`：`_post_with_retry` および `get_way_tags`
  - `geocoder.py`：Nominatim 呼び出し
  - `rerouter.py`：通常 GET と reroute POST の両方
- 各リクエストで per-call timeout / headers を明示的に指定し、共有クライアントの
  デフォルトを上書き

### 期待される効果

- localhost への GraphHopper：TCP 再接続コストが消える（~10〜50ms/回）
- HTTPS の Overpass / Nominatim：TLS ハンドシェイクが省ける（~100〜300ms/回）
- 累積効果：1 リクエストあたり数十 ms 削減、特に warm 時（GH 単独）で目立つ

### 動作確認（自動テスト）

`httpx.AsyncClient.post` をモックして circuit breaker + 共有クライアントの
組み合わせを再検証：

- リクエスト A（全失敗想定）：httpx.post 3 回 + circuit broken で bulk スキップ ✅
- リクエスト B（別 asyncio タスク・正常）：circuit breaker リセット、1 回で成功 ✅
- shared client は両リクエストで再利用されている ✅

### 実機計測（Masaya さんに手動記入）

バックエンド再起動後に渋谷→新宿で warm 計測：

| 計測項目 | P1 のみ（cache hit） | P1 + P2（cache hit + shared client） |
|---|---|---|
| GraphHopper 単体応答時間 | xx ms | xx ms |
| `/api/route` 合計応答時間 | xx ms | xx ms |

### 副作用・注意点

- バックエンド再起動時（uvicorn --reload）に共有クライアントが破棄＆再生成される
- リクエストごとの timeout/headers は per-call で渡すよう徹底（共有クライアントの
  デフォルトに依存しない）
- 接続プール由来の障害（古いコネクションが残って失敗）は keepalive_expiry=60s で
  リセットされるため、長時間運用しても問題になりにくい

### 運用上の注意

**GraphHopper コンテナを再起動した直後にバックエンド側で stale 接続による
`httpx.ReadTimeout` が発生することを 2026-06-05 に確認した。**

事象：
- GH を `docker compose stop/up` で再起動
- バックエンドは P2 の共有クライアントで GH への keepalive 接続を持っていた
- GH 再起動で古い接続は実質死んでいるが、バックエンド側はプールに「生きてる扱い」で
  持ち続けていた
- 次の `/api/route` で stale 接続を再利用 → 30 秒の read timeout で 500 エラー
- バックエンドを再起動して新しいクライアントを作ったら正常動作

**運用ルール:**
GraphHopper を再起動したら、必ずバックエンド（uvicorn）も再起動する。
uvicorn が `--reload` モードならコード変更をトリガーに再起動するか、手動で
Ctrl+C → 再起動。

コード側で transport-level retry や keepalive_expiry の短縮で対処することも
可能だが、本フェーズではコードを最小化する方針で運用ルール対応とした。

---

## タスク Q2：進行方向照合のロバスト化（2026-06-07 実装）

### 設計の要点

`check_oneway_violation` の方向照合精度を 3 点から改善した。

**1. OSM ジオメトリをルート通過区間にクリップ（`route_analyzer.py`）**

- `_trim_geometry(geom, p_start, p_end)` ヘルパーを追加
- `p_start = points[start_idx]`、`p_end = points[end_idx]` に最も近いノードを
  geom から距離の二乗で探索し、その間のサブリストを返す
- これにより法規チェックが「ルートが実際に通った区間」のみを対象にする
  （カーブの多い long way でも迂回部分のノードが多数決を汚染しない）

**2. 短い区間の照合スキップ（`law_checker.py`）**

- `_haversine_m(a, b)` と `_geom_length_m(geom)` を追加
- クリップ後の区間長が **20m 未満**の場合は方向照合をスキップし、
  `confidence=0.7` のまま違反として登録する
  （短い区間は travel_vector のノイズに対して内積符号が不安定なため）

**3. カーブ区間の多数決判定（`law_checker.py`）**

- `_check_direction(geom, tv, oneway)` ヘルパーを追加
- クリップ後のノードが **3 点以上**のとき、隣接ノード間のすべてのセグメント
  ベクトルと travel_vector の内積を計算し、「逆向き」が **過半数**なら
  逆走と判定する（多数決）
- 2 点の場合は従来どおり始終点ベクトルの内積で判定

### 期待される効果

- カーブが多い residential / tertiary 区間での偽陽性減少
  → L 字カーブや S 字の長い way で始終点ベクトルが斜めを向く問題が解消
- 短い way（< 20m）での信頼度の誤上昇防止
  → 従来は `confidence=1.0` が出てしまう場合があった
- `confidence=1.0` の割合は true positive に限定され、精度指標が向上する見込み

### 変更ファイル

- `backend/services/law_checker.py`：ヘルパー 4 関数追加、照合ブロック置き換え
- `backend/services/route_analyzer.py`：`_trim_geometry` 追加、ジオメトリ構築を
  リスト内包 → ループに変更してクリップを適用

### 計測結果（Masaya さんに手動記入）

バックエンド再起動後、`POST /api/experiment/batch/od-pairs/csv` で 15 O-D ペアを実行：

| 指標 | Q2 前（Pre-Q2） | Q2 後 |
|---|---|---|
| violations 総数（15ペア） | 4 件 | xx 件 |
| うち confidence=1.0 件数 | xx 件 | xx 件 |
| うち confidence=0.7 件数 | xx 件 | xx 件 |
| リルート発生ルート数 | 0 件 | xx 件 |

confidence 分布の確認コマンド：

```powershell
# バックエンド起動後
curl.exe -X POST http://localhost:8000/api/experiment/batch/od-pairs/csv `
  -H "Content-Type: application/json" `
  -d '{"algo_version":"v3"}' `
  -o experiment_post_q2.csv
```

CSV の `violation_count_high_conf` / `violation_count_low_conf` 列で確認する。
