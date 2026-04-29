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

## スマートフォンで動作確認する方法

PC と同じルーターに接続しているスマートフォンからアクセスする手順。
GPS 自動モード切り替えや走行中 UI の確認に使用する。

### 手順1: PC の IP アドレスを確認する

PowerShell または コマンドプロンプトで実行:

```powershell
ipconfig
```

「イーサネット アダプター」または「Wi-Fi アダプター」の
`IPv4 アドレス` を確認する（例: `192.168.1.5`）。

### 手順2: バックエンドを LAN に公開して起動

```bash
cd bicycle-navi/backend
uvicorn main:app --reload --port 8000 --host 0.0.0.0
```

> `--host 0.0.0.0` をつけることで、PC 以外からもアクセスできるようになる。

### 手順3: フロントエンドを LAN に公開して起動

```bash
cd bicycle-navi/frontend
npm run dev -- --host
```

起動すると以下のような表示が出る:

```
  ➜  Local:   http://localhost:5173/
  ➜  Network: http://192.168.1.5:5173/
```

スマートフォンのブラウザで `Network:` に表示された URL（例: `http://192.168.1.5:5173`）を開く。

> **仕組み**: フロントエンドの API リクエストは Vite の proxy 機能を通じて
> `http://localhost:8000` へ転送される。スマートフォン側でバックエンドの IP を
> 直接指定する必要はなく、CORS の問題も発生しない。

### 手順4: GPS（Geolocation）を使えるようにする

Geolocation API は **HTTPS 接続のみ**で動作する（localhost は例外）。
LAN の HTTP 接続でスマートフォンから使う場合は、以下のいずれかの方法で対処する。

#### 方法A: Android Chrome（簡易設定・推奨）

Android の Chrome で以下の手順を行う:

1. Chrome のアドレスバーに `chrome://flags` と入力して開く
2. `Insecure origins treated as secure` を検索する
3. テキストボックスにアクセスするURLを入力（例: `http://192.168.1.5:5173`）
4. フラグを **Enabled** に変更し、「Relaunch」を押す

これで HTTP でも Geolocation が動作するようになる。

#### 方法B: HTTPS を有効化（iOS Safari 含む全デバイス対応）

```bash
cd bicycle-navi/frontend
npm install -D @vitejs/plugin-basic-ssl
```

`frontend/vite.config.js` を以下のように更新:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'

export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
```

`npm run dev -- --host` で起動すると `https://192.168.1.5:5173` が使える。
ブラウザのセキュリティ警告が出た場合は「詳細設定 → 続行」で進める。

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
