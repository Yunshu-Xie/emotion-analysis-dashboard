# emotion-analysis-dashboard

日本語YouTubeコメント／テキストの感情分析ダッシュボード。Flask + DeepSeek(Ark) APIでコメントを感情・スラング・顔文字影響などの観点で解析し、Webダッシュボードとして可視化します。

## 構成

- `app.async.py` — メインアプリ。非同期(aiohttp)でAPIを並行呼び出しし、YouTubeコメント/ファイル/直接入力の3系統の分析フローと進捗ポーリングAPIを提供
- `emo2.py` — 旧版（同期処理 + pygal/pandasによる可視化）。参考用に残しているバージョン
- `emotion_dashboard.py` — Streamlitで作ったスタンドアロンのUIプロトタイプ（サンプルデータのみ）
- `templates/` — `upload.html`（入力フォーム）, `dashboard.html`（結果表示）, `error.html`
- `testapi.py` — Ark API呼び出し・レスポンス解析のスモークテスト
- `data.txt`, `api_response_1.json`, `api_response_2.json`, `ScMzIvxBSi4.json` — テスト用サンプルデータ
- `requirements.txt` — Streamlit版の依存関係

## セットアップ

```bash
pip install -r requirements.txt
pip install flask aiohttp openai youtube-comment-downloader
export ARK_API_KEY=your-api-key
python app.async.py
```

`http://localhost:80` でアクセス。

## 既知の制約 / 改善余地

- APIキーは環境変数 `ARK_API_KEY` で渡す想定（コード内にキーは含まれていません）
- `uploads/` 以下はアップロードされたファイルと解析結果の実行時キャッシュのため、リポジトリには含めていません
