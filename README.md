# emotion-analysis-dashboard

日本語YouTubeコメント／テキストの感情分析ダッシュボード。Flask + DeepSeek(Ark) APIでコメントを感情・スラング・顔文字影響などの観点で解析し、Webダッシュボードとして可視化します。

## 構成

- `app.async.py` — Flaskアプリ本体。非同期(aiohttp)でAPIを並行呼び出しし、YouTubeコメント/ファイル/直接入力の3系統の分析フローと進捗ポーリングAPIを提供
- `analysis.py` — プロンプト・API呼び出し・JSON解析・集計など、分析ロジック本体（`app.async.py` と `testapi.py` が共有）。レスポンスはAPI側のJSON Schema制約（`response_format: json_schema`）で構造を強制しており、文字列置換などの手製フォールバック解析はしていない
- `taskstore.py` — 分析タスクの状態（進捗・結果パス・エラー）をSQLiteに永続化する。プロセス再起動後も状態が残る
- `cleanup.py` — アップロードファイル／解析結果／タスク状態の定期クリーンアップ
- `templates/` — `upload.html`（入力フォーム）, `dashboard.html`（結果表示）, `error.html`
- `testapi.py` — Ark API呼び出し・レスポンス解析のスモークテスト
- `tests/` — `analysis.py` / `taskstore.py` / `cleanup.py` の単体テスト（`pytest tests/` で実行、ネットワーク不要）
- `data.txt`, `api_response_1.json`, `api_response_2.json`, `ScMzIvxBSi4.json` — テスト用サンプルデータ

## セットアップ

```bash
pip install -r requirements.txt
export ARK_API_KEY=your-api-key
python app.async.py
```

デフォルトは `http://127.0.0.1:5000`。`HOST` / `PORT` 環境変数で変更可能。

本番運用する場合は Flask の開発用サーバーではなく `gunicorn` / `waitress` などの WSGI サーバーを使い、リバースプロキシ（nginx等）でポート80/443を受けるようにしてください。

## 運用上の設定

- `TASK_TTL_SECONDS`（デフォルト21600秒=6時間）— この時間を過ぎたアップロードファイル・解析結果・タスク状態をバックグラウンドスレッドが自動削除します
- `TASKS_DB_PATH`（デフォルト `tasks.db`）— タスク状態を保存するSQLiteファイルのパス
- `/analyze` は同一IPから10分間に20リクエストまでの簡易レート制限があります（`app.async.py` の `RATE_LIMIT_*` で調整可能）

## 既知の制約

- APIキーは環境変数 `ARK_API_KEY` で渡す想定（コード内にキーは含まれていません）
- `uploads/` 以下はアップロードされたファイルと解析結果の実行時キャッシュのため、リポジトリには含めていません
