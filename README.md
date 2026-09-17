# YouTube Trend Analyzer

日本向けのゲーム動画を対象に、YouTube Data API v3から公開データを取得してトレンドを分析するStreamlitアプリです。

## 機能

- 日本の「ゲーム」カテゴリの人気動画一覧
- 任意キーワード（例: マイクラ / DeceasedCraft）の直近トレンド分析
- 複数ゲームタイトルの比較ランキング
- 3 / 6 / 12 / 24か月の月別長期トレンド分析
- 長尺 / Shorts候補（3分以下）の簡易分類
- 再生速度、Like率、コメント率、登録者数に対する再生比率
- 独自Trend Score
- CSV出力

## セットアップ

### 1. YouTube Data APIキーを取得

Google Cloud Consoleでプロジェクトを作成し、YouTube Data API v3を有効化してAPIキーを作成してください。

APIキーはGitHub等に公開しないでください。

### 2. インストール

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. 起動

```bash
streamlit run app.py
```

ブラウザが開いたら、左側の入力欄にYouTube Data API Keyを入力します。

環境変数を使う場合:

```bash
# macOS / Linux
export YOUTUBE_API_KEY="YOUR_API_KEY"
streamlit run app.py
```

## Trend Scoreについて

YouTube公式のスコアではありません。

動画単位では以下を相対評価します。

- 公開後1時間あたりの再生数: 45%
- Like率: 25%
- コメント率: 15%
- チャンネル登録者数に対する再生数: 15%

ゲーム比較では以下を相対評価します。

- 中央値再生速度: 55%
- 投稿チャンネルの広がり: 25%
- 中央値Like率: 20%

大規模チャンネルだけでランキングが埋まるのを避けるため、単純な総再生数だけでは順位を決めていません。

## 長期分析について

長期タブでは1か月ごとに検索を行い、その月の上位動画をサンプリングします。

例えば24か月分析なら、基本的に24回の `search.list` 呼び出しが必要です。複数ゲームを2年間まとめて分析したい場合はAPIクォータに注意してください。

またYouTube Data APIは「YouTube全動画の完全な履歴データベース」ではありません。検索結果は上位サンプルなので、数値は絶対的な市場規模ではなく、ゲーム同士や時系列の相対比較に使うことを想定しています。

## 次に追加できる機能

- 過去2年間の「継続投稿 × 継続視聴」ゲームランキング
- チャンネル成長率の定点観測
- タイトル / サムネイル文言の頻出語分析
- 急上昇キーワード検出
- Shortsと長尺の完全分離
- SQLite / PostgreSQLへの日次蓄積
- 毎日自動収集するスケジューラ
- AIによる「なぜ伸びたか」の自動要約
- YouTubeチャンネルURL入力による競合分析


## 無料Web公開（Streamlit Community Cloud）

1. このフォルダ一式をGitHubリポジトリへアップロードします。
2. Streamlit Community CloudでGitHubを接続します。
3. `app.py` をエントリポイントにしてDeployします。
4. APIキーを画面入力したくない場合は、Community CloudのSecretsに次を登録します。

```toml
YOUTUBE_API_KEY = "YOUR_API_KEY"
```

APIキーをGitHubのソースコードへ直接書かないでください。

### 無料運用時の注意
YouTube Data APIにはクォータがあります。特に検索APIを大量に使う長期分析・多数ゲーム比較は回数を抑えてください。まずは1回5〜10タイトル、直近7〜30日の分析を中心に使う想定です。
