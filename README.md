# 自転車ナビゲーションシステム 卒業研究

青山学院大学・宮治研究室（2026年度）  
日本の交通法規（一方通行・歩道通行・二段階右折など）に準拠した自転車ナビゲーションシステム

---

## システム構成

```
bicycle-navi/
  backend/      Python + FastAPI（法規判定・ルーティングロジック）
  frontend/     React + Leaflet.js（地図UI）
  graphhopper/  設定ファイル（バイナリキャッシュはgit管理外）
  docker-compose.yml
```

外部サービス:
- **GraphHopper**（Dockerでセルフホスト）: 自転車ルーティングエンジン
- **Overpass API**（無料）: OSMタグ取得・法規判定に使用

---

## 別の端末でセットアップする手順

### 前提条件

以下をあらかじめインストールしておくこと:

| ツール | 推奨バージョン | 用途 |
|--------|--------------|------|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | 最新版 | GraphHopperの起動 |
| Python | 3.13以上 | バックエンド |
| Node.js | 18以上 | フロントエンド |
| Git | 最新版 | リポジトリのクローン |

---

### 手順1: リポジトリをクローン

```bash
git clone <リポジトリURL>
cd 卒研
```

---

### 手順2: GraphHopperを起動

> **注意**: 初回起動時はOSMデータのダウンロード＋グラフビルドが走るため、**30分〜1時間**かかる。

```bash
cd bicycle-navi
docker-compose up -d
```

起動確認（グラフビルド完了後に成功する）:

```bash
curl "http://localhost:8989/route?point=35.6762,139.6503&point=35.6895,139.6917&profile=bike&locale=ja"
```

JSONレスポンスが返ってきたら成功。

> **グラフキャッシュについて**: ビルド完了後、`graphhopper/default-gh/` にキャッシュが生成される。  
> このキャッシュはgit管理対象外（`.gitignore`に記載）。  
> 2回目以降の `docker-compose up` はキャッシュを再利用するため数秒で起動する。

---

### 手順3: Pythonバックエンドをセットアップ

プロジェクトルート（`卒研/`）で仮想環境を作成する:

```bash
# プロジェクトルートで実行（bicycle-navi/ の中ではない）
python -m venv .venv
```

仮想環境を有効化:

```bash
# Windows（PowerShell）
.venv\Scripts\Activate.ps1

# Windows（bash / Git Bash）
source .venv/Scripts/activate

# macOS / Linux
source .venv/bin/activate
```

依存パッケージをインストール:

```bash
pip install -r bicycle-navi/backend/requirements.txt
```

バックエンドを起動:

```bash
cd bicycle-navi/backend
uvicorn main:app --reload --port 8000
```

`http://localhost:8000/docs` でSwagger UIが開けばOK。

---

### 手順4: Reactフロントエンドをセットアップ

```bash
cd bicycle-navi/frontend
npm install
npm run dev
```

`http://localhost:5173` でアプリが表示されればOK。

---

### 動作確認

アプリを開いて以下の住所で検索:

- 出発地: `渋谷駅`
- 目的地: `新宿駅`

青いルートが地図に表示され、法規違反箇所に赤いマーカーが出れば正常動作。

---

## 起動コマンド まとめ（2回目以降）

ターミナルを**3つ**開き、それぞれ以下を実行:

| ターミナル | コマンド | URL |
|-----------|---------|-----|
| 1（Docker） | `cd bicycle-navi && docker-compose up` | http://localhost:8989 |
| 2（バックエンド） | `cd bicycle-navi/backend && uvicorn main:app --reload --port 8000` | http://localhost:8000/docs |
| 3（フロントエンド） | `cd bicycle-navi/frontend && npm run dev` | http://localhost:5173 |

---

## git管理対象外のファイル（注意）

以下はgitにコミットされていない。別端末での作業時は手動で準備が必要:

| ファイル / ディレクトリ | 理由 | 対処 |
|----------------------|------|------|
| `.venv/` | Python仮想環境（環境依存） | `python -m venv .venv` → `pip install` |
| `bicycle-navi/frontend/node_modules/` | npmパッケージ（大容量） | `npm install` |
| `bicycle-navi/graphhopper/default-gh/` | GHグラフキャッシュ（バイナリ・大容量） | `docker-compose up` で自動生成 |
| `bicycle-navi/graphhopper/*.osm.pbf` | OSMデータ（数百MB） | docker-compose起動時に自動ダウンロード |
| `.env` 等 | APIキーなどの機密情報（存在する場合） | 別途共有 |

---

## トラブルシューティング

### GraphHopperが起動しない
- Docker Desktopが起動しているか確認
- `docker-compose logs` でエラーを確認
- グラフビルド中は `/route` エンドポイントが503を返すので、完了まで待つ
- DockerなしでGraphHopper Public APIを使う場合は `backend/services/graphhopper.py` の `GH_BASE` を変更する

### バックエンドで `ModuleNotFoundError`
- `.venv` が有効化されているか確認（プロンプトの先頭に `(.venv)` が出るはず）
- `pip install -r bicycle-navi/backend/requirements.txt` を再実行

### フロントエンドが起動しない
- `bicycle-navi/frontend/` で `npm install` を実行したか確認

### Overpass APIがタイムアウトする
- Overpassの公開サーバーは混雑時に遅延や504エラーを返す
- バックエンドは複数エンドポイントを自動フォールバックする実装済み
- タイムアウトした場合は法規チェックをスキップしてルートのみ表示される（正常な挙動）

### リルートが遅い（数秒〜十数秒かかる）
- 法規準拠ルート計算時はGraphHopperのCHを無効化しているため、Dijkstra/A*で計算される
- これは既知の制限（論文にも記載予定）

---

## 実装済み機能

- [x] 一方通行（`oneway`）違反検出
- [x] 歩道通行可否（`sidewalk`）チェック
- [x] 自転車レーン（`cycleway`）推奨表示
- [x] 二段階右折要否判定（`highway` + `lanes`）
- [x] 法規違反エッジを避けたリルート
- [x] 住所・地名入力（Nominatimジオコーディング）
- [x] 評価実験用バッチ比較データ出力（CSV/JSON）
- [ ] スマートフォン対応UI
