# ---------- app_async.py ----------
import asyncio
import json
import logging
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import islice

from flask import Flask, jsonify, render_template, request, url_for
from youtube_comment_downloader import (SORT_BY_POPULAR, SORT_BY_RECENT,
                                         YoutubeCommentDownloader)

import analysis
from analysis import (analyze_data, create_visualizations, extract_video_id,
                       generate_summary, validate_youtube_url)
from cleanup import cleanup_loop
from taskstore import TaskStore

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['CONCURRENCY'] = 10  # 并发请求数
app.config['MAX_COMMENTS'] = 1000  # 最大评论数
app.config['TASK_TTL_SECONDS'] = int(os.getenv('TASK_TTL_SECONDS', 6 * 3600))  # 任务/文件保留时长

executor = ThreadPoolExecutor(max_workers=4)
task_store = TaskStore(os.getenv('TASKS_DB_PATH', 'tasks.db'))

# 简易限流：每个IP在窗口期内允许的最大请求数
RATE_LIMIT_WINDOW_SECONDS = 600
RATE_LIMIT_MAX_REQUESTS = 20
_rate_limit_hits = defaultdict(list)
_rate_limit_lock = threading.Lock()


def _is_rate_limited(ip):
    now = time.time()
    with _rate_limit_lock:
        hits = [t for t in _rate_limit_hits[ip] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        hits.append(now)
        _rate_limit_hits[ip] = hits
        return len(hits) > RATE_LIMIT_MAX_REQUESTS


async def process_file_async(filepath, task_id):
    """异步处理文件（稳定增强版）"""
    results = []
    failed = []
    semaphore = asyncio.Semaphore(app.config['CONCURRENCY'])

    async def process_line(line):
        async with semaphore:
            for retry in range(2):
                try:
                    result = await analysis.async_analyze(line.strip())
                    if result and not result.get('__error'):
                        return line, result
                    app.logger.warning(f"分析结果异常（重试 {retry+1}）: {line[:50]}...")
                except Exception as e:
                    app.logger.error(f"行处理失败: {e} | 内容: {line[:50]}...")
                await asyncio.sleep(1)
            return line, None

    app.logger.debug(f"[处理开始] 文件: {filepath}")

    encodings = ['utf-8', 'utf-8-sig', 'shift_jis', 'euc-jp', 'cp932']
    lines = []
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding, errors='replace') as f:
                lines = [line.strip() for line in f if line.strip()]
                if lines:
                    break
        except Exception as e:
            app.logger.warning(f"编码尝试失败 [{encoding}]: {e}")

    if not lines:
        raise ValueError("無効なファイル内容またはサポート外の文字コード")

    total_lines = len(lines)
    processed = 0
    batch_size = max(10, min(50, total_lines // 10))

    for batch_num, i in enumerate(range(0, total_lines, batch_size), 1):
        batch = lines[i:i + batch_size]
        app.logger.debug(f"处理批次 {batch_num} (行 {i+1}-{i+len(batch)})")

        tasks = [process_line(line) for line in batch]
        for future in asyncio.as_completed(tasks):
            line, result = await future
            processed += 1

            if result:
                results.append({"content": line, "analysis": result})
            else:
                failed.append(line)

            task_store.update_progress(task_id, processed / total_lines)

    if not results:
        raise ValueError("分析可能なデータがありません")

    output_path = f"uploads/{task_id}_result.json"
    tmp_path = f"{output_path}.tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump({
                "results": results,
                "failed": failed,
                "metadata": {
                    "total": len(lines),
                    "success_rate": f"{(len(results)/len(lines))*100:.1f}%",
                },
            }, f, ensure_ascii=False)
        os.rename(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    app.logger.info(f"文件处理完成: 成功 {len(results)} 条, 失败 {len(failed)} 条")
    return output_path


def run_async_task(task_func, *args):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(task_func(*args))
    finally:
        loop.close()


def run_task_and_record(filepath, task_id):
    """バックグラウンドスレッドで実行し、結果をタスクDBに反映する。"""
    try:
        result_path = run_async_task(process_file_async, filepath, task_id)
        task_store.mark_completed(task_id, result_path)
    except Exception as e:
        app.logger.error(f"任务失败 [{task_id}]: {e}")
        task_store.mark_error(task_id, str(e))


def start_processing(task_id, filepath):
    display_options = request.form.getlist('display_options')
    task_store.create_task(task_id, filepath, display_options)
    executor.submit(run_task_and_record, filepath, task_id)
    return jsonify({"status": "processing", "task_id": task_id}), 202


@app.route('/analyze', methods=['POST'])
def handle_analysis():
    if _is_rate_limited(request.remote_addr):
        return jsonify({"error": "リクエストが多すぎます。しばらく待ってから再試行してください"}), 429

    input_type = request.form.get('input_type', 'file')
    task_id = str(time.time_ns())

    try:
        if input_type == 'youtube':
            video_url = request.form['youtube_url']
            if not validate_youtube_url(video_url):
                return jsonify({"error": "無効なYouTube URL形式"}), 400

            video_id = extract_video_id(video_url)
            if not video_id:
                return jsonify({"error": "動画IDの抽出に失敗しました"}), 400

            downloader = YoutubeCommentDownloader()
            comments = downloader.get_comments_from_url(
                video_url,
                sort_by=SORT_BY_POPULAR if request.form.get('sort_by') == 'popular' else SORT_BY_RECENT
            )

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}.txt")
            with open(filepath, 'w', encoding='utf-8') as f:
                for comment in islice(comments, app.config['MAX_COMMENTS']):
                    try:
                        text = comment.get('text', '').strip()
                        if text:
                            f.write(text + '\n')
                    except UnicodeEncodeError:
                        app.logger.warning(f"编码错误跳过评论: {comment.get('comment_id')}")
                    except Exception as e:
                        app.logger.error(f"评论处理异常: {e}")

        elif input_type == 'file':
            if 'file' not in request.files or request.files['file'].filename == '':
                return jsonify({"error": "ファイルが選択されていません"}), 400

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}.txt")
            request.files['file'].save(filepath)

        else:
            text_content = request.form.get('text', '')
            lines = [line.strip() for line in text_content.split('\n') if line.strip()]
            if not lines:
                return jsonify({"error": "有効なテキストが含まれていません"}), 400

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}.txt")
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))

        return start_processing(task_id, filepath)

    except Exception as e:
        app.logger.error(f"处理请求失败: {e}")
        return jsonify({"error": f"サーバーエラー: {e}"}), 500


@app.route('/status/<task_id>')
def analysis_status(task_id):
    task = task_store.get_task(task_id)
    if not task:
        return jsonify({"status": "error", "message": "無効なタスクID"}), 404

    if task['status'] == 'processing' and time.time() - task['created_at'] > 1800:
        return jsonify({"status": "error", "message": "処理タイムアウト"})

    if task['status'] == 'completed':
        return jsonify({
            "status": "completed",
            "redirect": url_for('show_result', task_id=task_id),
        })
    elif task['status'] == 'error':
        return jsonify({"status": "error", "message": task['error_message'] or "結果生成失敗"})
    else:
        return jsonify({"status": "processing", "progress": task['progress']})


@app.route('/result/<task_id>')
def show_result(task_id):
    task = task_store.get_task(task_id)
    if not task or not task['result_path'] or not os.path.exists(task['result_path']):
        return render_template('error.html', message="结果不存在")

    with open(task['result_path'], encoding='utf-8') as f:
        data = json.load(f)

    display_options = task['display_options']

    analysis_result = analyze_data(data['results'])
    visualizations = create_visualizations(analysis_result)
    ai_summary = generate_summary(analysis_result, data['results'])

    source_type = 'youtube' if '_youtube' in task_id else \
                  'file' if '_file' in task_id else 'text'

    return render_template(
        'dashboard.html',
        source_type=source_type,
        analysis=analysis_result,
        charts=visualizations,
        total=analysis_result['total'],
        comments=data['results'][:100],
        ai_summary=ai_summary,
        display_options=display_options,
    )


@app.route('/', methods=['GET'])
def index():
    return render_template('upload.html')


@app.after_request
def add_header(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    cleanup_thread = threading.Thread(
        target=cleanup_loop,
        args=(task_store, app.config['UPLOAD_FOLDER'], app.config['TASK_TTL_SECONDS']),
        daemon=True,
    )
    cleanup_thread.start()

    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '5000'))
    app.run(host=host, port=port, threaded=True)
