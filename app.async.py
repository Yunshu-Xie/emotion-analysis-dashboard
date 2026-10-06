# ---------- app_async.py ----------
import os
import json
import time
import asyncio
import aiohttp
from flask import Flask, render_template, request, redirect, url_for,jsonify
from openai import OpenAI
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import uuid
import re
from datetime import datetime
import traceback
import sys
from itertools import islice
from youtube_comment_downloader import YoutubeCommentDownloader, SORT_BY_POPULAR, SORT_BY_RECENT

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['ARK_API_KEY'] = os.getenv('ARK_API_KEY', 'your-api-key')
app.config['CONCURRENCY'] = 10  # 并发请求数
app.config['REQUEST_TIMEOUT'] = 30  # 单请求超时时间
app.config['MAX_COMMENTS'] = 1000  # 最大评论数

# 异步处理相关配置
executor = ThreadPoolExecutor(max_workers=4)
processing_tasks = {}  # 存储处理任务的Future

client = OpenAI(
    api_key=app.config['ARK_API_KEY'],
    base_url="https://ark.cn-beijing.volces.com/api/v3"
)


# 情感分析prompt模板（日语）
ANALYSIS_PROMPT = """※※ 厳密にJSONフォーマットを遵守 ※※
{{
  "分析": {{
    "要約": "{{
      [句子的核心事实]
      ▼ 例:
      • ユーザーが衣装デザインを具体的に称賛
      • 動画編集のテンポに関する指摘
      • ゲスト出演者へのリクエスト
    }}",
    "基本感情": "ポジティブ/中立/ネガティブ",
    "核心视点": ["キーワード"],
    "感情分析": ["タグ"],
    "顔文字影響": 0-3,
    "潜在意図": 0-20,
    "礼儀レベル": "砕けた/普通/丁寧"
  }}
}}

▼ 生成ルール (厳守):
1. 要約は事実のみを記載（意見/感情表現を排除）
2. 具体的事項に焦点（例: 「2:15のカメラワーク」）
3. 動詞で終わる文末（例: ～を指摘/～を提案/～を要求）
4. 要約は20文字以内の簡潔表現
5. 「核心视点」字段必须包含从文本中提取的1个最具代表性的观点关键词
6. 核心视点关键词选择标准：
   - 反映明确立场（例：支持/反对）
   - 表达情感倾向（例：喜欢/失望） 
   - 包含具体诉求（例：改进建议）
   - 使用名词或动词的基本形
7. 核心视点关键词需满足：
   - 长度2-8个字符
   - 排除网络流行语
   - 避免抽象词汇

分析対象:
{content}"""


# 在ANALYSIS_PROMPT后添加总结prompt
SUMMARY_PROMPT = """📊 ネットコメント分析レポート生成のお願い
(不添加注释，不使用markdown)
【基本統計】
- 総コメント数: {total}
- 感情分布: {sentiment_distribution}
- 上位感情: {top_emotions}
- 主要ネット用語: {top_slangs}

【生成要件】
▼ 主要ポイント（3点以内）
• 全体的な評価傾向（例: 70%がポジティブ）
• 注目すべき文化要素（例: アニメ関連用語多数）
• 改善提案（具体例1つ）

▼ 形式要件
- 200文字以内
- 絵文字を適度に使用（例: 👍）
- 見出し記号「▶」使用

実際の分析結果："""

async def async_analyze(content):

    messages = [{
        "role": "user",
        "content": ANALYSIS_PROMPT.format(content=content)  # 使用更新后的模板
    }]

    async with aiohttp.ClientSession() as session:
        for attempt in range(3):
            try:
                start_time = time.time()
                
                async with session.post(
                    "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                    json={
                        "model": "deepseek-v3-241226",
                        "messages": messages,
                        "temperature": 0.3,
                        "max_tokens": 1024,
                        "response_format": {"type": "json_object"}
                    },
                    headers={
                        "Authorization": f"Bearer {app.config['ARK_API_KEY']}",
                        "Content-Type": "application/json"
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    # 基础状态验证
                    if response.status != 200:
                        error_text = await response.text()
                        app.logger.error(f"HTTP错误 {response.status}: {error_text[:200]}...")
                        continue

                    # 多模式解析增强
                    data = await response.json()
                    raw_content = data['choices'][0]['message']['content']
                    
                    # 定义多模式解析器
                    def parse_response(content):
                        # 模式1：直接解析
                        try:
                            return json.loads(content), "直接JSON"
                        except json.JSONDecodeError:
                            pass

                        # 模式2：提取包裹JSON
                        json_match = re.search(r'({.*})', content, re.DOTALL)
                        if json_match:
                            try:
                                return json.loads(json_match.group(1)), "包裹JSON"
                            except Exception as e:
                                app.logger.debug(f"包裹解析失败: {str(e)}")

                        # 模式3：容错处理
                        try:
                            sanitized = content.replace("'", '"').replace("\n", "")
                            return json.loads(sanitized), "修正JSON"
                        except Exception as e:
                            app.logger.debug(f"容错解析失败: {str(e)}")
                            return None, None

                    parsed_data, parse_type = parse_response(raw_content)
                    
                    # 解析结果验证
                    if not parsed_data:
                        raise ValueError("所有解析模式均失败")
                    
                    app.logger.debug(f"解析成功（模式：{parse_type}）")
                    
                    # 数据结构验证
                    analysis = parsed_data.get('分析', {})
                    if not analysis:
                        raise ValueError("响应缺少分析字段")
                        
                    if analysis.get("基本感情") not in ["ポジティブ", "中立", "ネガティブ"]:
                        raise ValueError(f"无效情感值：{analysis.get('基本感情')}")

                    # 类型强制转换
                    return {
                        "分析": {
                            "要約": str(analysis.get("要約", "要約生成失敗")),  # 新增字段
                            "基本感情": str(analysis["基本感情"]),
                            "感情詳細": list(map(str, analysis.get("感情詳細", []))),
                            "核心视点": list(map(str, analysis.get("核心视点", []))),
                            "顔文字影響": int(analysis.get("顔文字影響", 0)),
                            "潜在意図": int(analysis.get("潜在意図", 0)),  # 新增
                            "礼儀レベル": str(analysis.get("礼儀レベル", "普通"))
                        }
                    }

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                wait_time = 2 ** attempt
                app.logger.warning(f"网络错误 ({type(e).__name__})，{wait_time}s后重试...")
                await asyncio.sleep(wait_time)
                
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                error_type = type(e).__name__
                app.logger.error(f"解析失败 [{error_type}]: {str(e)}")
                if attempt == 2:  # 最后一次尝试时记录原始内容
                    app.logger.debug(f"原始响应内容：{raw_content[:200]}...")
                
            finally:
                # 精确速率控制（300 RPM）
                elapsed = time.time() - start_time
                await asyncio.sleep(max(0.0, 0.2 - elapsed))

    # 降级返回（带错误标记）
    return {
        "分析": {
            "基本感情": "中立",
            "感情詳細": ["解析失败"],
            "ネットスラング": [],
            "顔文字影響": 0,
            "礼儀レベル": "普通",
            "__error": True
        }
    }


async def process_file_async(filepath, task_id):
    """异步处理文件（稳定增强版）"""
    results = []
    failed = []
    semaphore = asyncio.Semaphore(app.config['CONCURRENCY'])
    
    async def process_line(line):
        """单行处理函数（带重试机制）"""
        async with semaphore:
            for retry in range(2):  # 单行重试机制
                try:
                    result = await async_analyze(line.strip())
                    if result and result.get('分析'):
                        return line, result
                    app.logger.warning(f"分析结果异常（重试 {retry+1}）: {line[:50]}...")
                except Exception as e:
                    app.logger.error(f"行处理失败: {str(e)} | 内容: {line[:50]}...")
                await asyncio.sleep(1)  # 重试间隔
            return line, None

    app.logger.debug(f"[处理开始] 文件: {filepath}")

    # 强化文件读取（支持更多编码）
    encodings = ['utf-8', 'utf-8-sig', 'shift_jis', 'euc-jp', 'cp932']
    lines = []
    
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding, errors='replace') as f:
                lines = [line.strip() for line in f if line.strip()]
                if lines: break
        except Exception as e:
            app.logger.warning(f"编码尝试失败 [{encoding}]: {str(e)}")
    
    if not lines:
        raise ValueError("無効なファイル内容またはサポート外の文字コード")

    app.logger.debug(f"有效行数: {len(lines)}")
    
    total_lines = len(lines)
    processed = 0
    
    # 动态批次处理（根据行数调整）
    batch_size = max(10, min(50, total_lines//10))
    app.logger.debug(f"动态批次大小: {batch_size}")
    
    for batch_num, i in enumerate(range(0, total_lines, batch_size), 1):
        batch = lines[i:i + batch_size]
        app.logger.debug(f"处理批次 {batch_num} (行 {i+1}-{i+len(batch)})")
        
        # 创建异步任务
        tasks = [process_line(line) for line in batch]
        
        # 实时进度更新
        for future in asyncio.as_completed(tasks):
            line, result = await future
            processed += 1
            
            # 更新进度（添加平滑过渡）
            current_progress = processed / total_lines
            processing_tasks[task_id]['progress'] = min(current_progress + 0.05, 1.0)  # 防止卡在99%
            
            # 结果处理
            if result and result.get('分析') and not result['分析'].get('__error', False):
                results.append({
                    "content": line,
                    "analysis": result['分析']
                })
                app.logger.debug(f"处理成功: {line[:30]}...")
            else:
                failed.append(line)
                app.logger.warning(f"处理失败: {line[:30]}...")
                app.logger.debug(f"失败详情: {result.get('分析', {}).get('__error_reason', '未知错误') if result else '无结果'}")
            
            # 精确进度更新
            processing_tasks[task_id]['progress'] = processed / total_lines

    # 空结果保护
    if not results:
        app.logger.error("完全な分析失敗 - すべての行の処理に失敗")
        raise ValueError("分析可能なデータがありません")

    # 保存结果（原子操作）
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
                    "processed_at": datetime.now().isoformat()
                }
            }, f, ensure_ascii=False)
        
        # 原子重命名
        os.rename(tmp_path, output_path)
    except Exception as e:
        app.logger.error(f"结果保存失败: {str(e)}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    app.logger.info(f"文件处理完成: 成功 {len(results)} 条, 失败 {len(failed)} 条")
    return output_path


def run_async_task(task_func, *args):
    """在事件循环中运行异步任务"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(task_func(*args))

def analyze_data(data):
    analysis = defaultdict(lambda: defaultdict(int))
    analysis['total'] = len(data)  # 直接使用数据条数作为总数

    if analysis['total'] == 0:  # 提前处理空数据
        return {
            'total': 0,
            'sentiment_counts': {'ポジティブ':0, '中立':0, 'ネガティブ':0},
            'emotion_freq': defaultdict(int),
            'slang_freq': defaultdict(int)
        }

    for entry in data:
    # 情感详细处理增强
        emotions = entry['analysis'].get('感情詳細', [])
        for emotion in emotions:
            if emotion and str(emotion).strip():  # 过滤空值
                clean_emotion = str(emotion).strip()[:20]  # 限制长度
                analysis['emotion_freq'][clean_emotion] += 1


    for entry in data:
        # 核心观点统计（新增）
        for keyword in entry['analysis'].get('核心视点', []):
            if keyword and str(keyword).strip():
                clean_keyword = str(keyword).strip()[:20]
                analysis['viewpoint_freq'][clean_keyword] += 1
            
    
    total = 0
    for entry in data:
        total += 1
        analysis['sentiment_counts'][entry['analysis'].get('基本感情', '')] += 1
        for emotion in entry['analysis'].get('感情詳細', []):
            analysis['emotion_freq'][emotion] += 1
        for slang in entry['analysis'].get('ネットスラング', []):
            analysis['slang_freq'][slang] += 1
    
    analysis['total'] = total

    if not analysis['sentiment_counts']:
        analysis['sentiment_counts'] = {'ポジティブ': 0, '中立': 0, 'ネガティブ': 0}
    
    if not analysis['emotion_freq']:
        analysis['emotion_freq']['（データなし）'] = 0

    return analysis

def create_visualizations(analysis):
    """生成观点词云数据"""
    viewpoint_items = sorted(
        analysis['viewpoint_freq'].items(),
        key=lambda x: -x[1]
    )[:100]  # 取前100个高频观点
    
    viewpoint_data = [{'text': k, 'value': v} for k, v in viewpoint_items]
    
    return {
        'viewpoint_data': viewpoint_data
    }



def generate_summary(analysis, raw_data):
    """基于原始评论生成总结报告（增强版）"""
    try:
        # 提取前100条原始评论（带异常处理）
        raw_comments = []
        try:
            raw_comments = [
                str(entry.get('content', '')).strip() 
                for entry in raw_data[:100] 
                if entry.get('content')
            ]
            # 过滤空内容和异常编码
            raw_comments = [
                re.sub(r'[\x00-\x1F\x7F-\x9F]', '', c) 
                for c in raw_comments 
                if c and len(c) >= 2
            ][:100]  # 二次截断确保数量
        except Exception as e:
            app.logger.error(f"原始评论提取失败: {str(e)}")
            traceback.print_exc()

        # 空数据保护
        if not raw_comments:
            return "⚠️ 分析対象のコメントが見つかりません\n考えられる原因:\n• コメント抽出エラー\n• 文字コード問題"

        # 构建带编号的评论列表（优化可读性）
        comment_list = []
        for idx, comment in enumerate(raw_comments, 1):
            # 控制单条评论长度
            trimmed = (comment[:120] + '...') if len(comment) > 120 else comment
            comment_list.append(f"{idx:03d}. {trimmed}")
        
        comment_text = "\n".join(comment_list)

        # 多语言检测（针对非日语评论）
        non_jp_chars = sum(1 for c in ''.join(raw_comments) if ord(c) > 255)
        lang_note = ""
        if non_jp_chars / len(''.join(raw_comments)) > 0.3:
            lang_note = "※英語コメントが含まれている場合は適宜翻訳してください\n"

        # 优化后的prompt模板
        SUMMARY_PROMPT = f"""📊 コメント分析レポート生成指示
以下の実際のコメント(100件)を精読し、以下の要素を含むレポートを生成（以纯文字格式，不准使用markdown）：

▼ 分析要件
1. 全体の評価傾向（ポジティブ/ネガティブの比率）
2. 3つの主要テーマ（例: 編集テンポ・衣装デザイン）
3. 具体的な改善要望（最も多く言及されているもの）
4. 特徴的な表現パターン
5. ユーザーの隠れたニーズ

▼ 形式要件
• 箇条書き3~5項目
• 実際のコメントから1つ以上引用（"..."で明記）
• 400文字以内
• 絵文字を適度に使用
• 見出し記号「▶」を使用

例：
コメント分析
・動画全体における感情分布は、ポジティブ 39.8％・中立 58.2％・ネガティブ 2.1％と推移しており、肯定的な反応が優勢を占めております。
・ポジティブ感情の主要因として「懐かしさ」「面白さ」に関する言及が突出しており、特に高度な映像編集技術が視聴者層から絶賛の的となっている点が特筆されます。
・ネガティブ感情に関しては「過激な表現」「予期せぬ演出」などに対する驚愕反応が主な要因として挙げられ、一定の視聴者層に違和感を生じさせている実態がうかがえます。
・改善提案としましては、現状の映像クオリティを維持しつつ、過激性の強い要素を段階的に抑制。既存のデザインコンセプトを維持しつつ、より共感性の高いナチュラルな表現手法への移行が効果的と推察されます。

{lang_note}▼ 分析対象コメント:
{comment_text}

▼ 分析結果："""

        # 带重试机制的API调用
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model="deepseek-v3-241226",
                    messages=[{
                        "role": "user",
                        "content": SUMMARY_PROMPT
                    }],
                    temperature=0.5,
                    max_tokens=800,
                    timeout=30
                )
                
                # 清理响应内容
                summary = response.choices[0].message.content.strip()
                cleaned_summary = re.sub(
                    r'(\n{2,})', '\n',  # 合并空行
                    summary.replace('※', '')  # 去除注释符号
                ).strip()

                # 添加原始数据标记
                return f"{cleaned_summary}"

            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait_time = 2 ** attempt
                app.logger.warning(f"API调用失败（{str(e)}），{wait_time}s后重试...")
                time.sleep(wait_time)

    except Exception as e:
        error_msg = f"""⚠️ レポート生成エラー
【原因】{str(e)}
【対処法】
1. コメントを短く分割
2. 再試行してください
3. 問題が続く場合は管理者へ連絡"""
        app.logger.error(f"总结生成失败: {traceback.format_exc()}")
        return error_msg

    # 最终失败保护
    return "レポート生成に失敗しました。入力データの形式をご確認ください"


def validate_youtube_url(url):
    """验证YouTube URL格式"""
    regex = r'^(https?\:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+'
    return re.match(regex, url) is not None

def extract_video_id(url):
    """提取YouTube视频ID"""
    reg_exp = r'^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*'
    match = re.match(reg_exp, url)
    return (match.group(2) if match and len(match.group(2)) == 11 else None)

def process_common_analysis(task_id, filepath):
    """通用分析处理"""
    future = executor.submit(
        run_async_task,
        process_file_async,
        filepath,
        task_id
    )
    
    processing_tasks[task_id] = {
        'future': future,
        'progress': 0.0,
        'filepath': filepath,
        'start_time': time.time(),
        'display_options': request.form.getlist('display_options')
    }
    
    return jsonify({
        "status": "processing",
        "task_id": task_id
    }), 202



@app.route('/analyze', methods=['POST'])
def handle_analysis():
    input_type = request.form.get('input_type', 'file')
    display_options = request.form.getlist('display_options')
    task_id = str(time.time_ns())

    try:
        if input_type == 'youtube':
            # YouTube评论处理
            video_url = request.form['youtube_url']
            
            if not validate_youtube_url(video_url):
                return jsonify({"error": "無効なYouTube URL形式"}), 400
                
            video_id = extract_video_id(video_url)
            if not video_id:
                return jsonify({"error": "動画IDの抽出に失敗しました"}), 400

            # 下载评论
            downloader = YoutubeCommentDownloader()
            comments = downloader.get_comments_from_url(
                video_url,
                sort_by=SORT_BY_POPULAR if request.form.get('sort_by') == 'popular' else SORT_BY_RECENT
            )
            
            # 保存到临时文件
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}.txt")
            with open(filepath, 'w', encoding='utf-8') as f:
                count = 0
                for comment in islice(comments, app.config['MAX_COMMENTS']):
                    try:
                        text = comment.get('text', '').strip()
                        if text:
                            f.write(text + '\n')
                            count += 1
                    except UnicodeEncodeError:
                        app.logger.warning(f"编码错误跳过评论: {comment.get('comment_id')}")
                    except Exception as e:
                        app.logger.error(f"评论处理异常: {str(e)}")
                        continue
            
            return process_common_analysis(task_id, filepath)

        elif input_type == 'file':
            # 文件上传处理
            if 'file' not in request.files:
                return jsonify({"error": "ファイルが選択されていません"}), 400
                
            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "無効なファイル名"}), 400
                
            # 保存文件
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}.txt")
            file.save(filepath)
        else:
            # 文本输入处理
            text_content = request.form.get('text', '')
            if not text_content.strip():
                return jsonify({"error": "テキストが入力されていません"}), 400
                
            # 创建临时文件
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}.txt")
            with open(filepath, 'w', encoding='utf-8') as f:
                # 按行分割并过滤空行
                lines = [line.strip() for line in text_content.split('\n') if line.strip()]
                if not lines:
                    return jsonify({"error": "有効なテキストが含まれていません"}), 400
                f.write('\n'.join(lines))

        # 启动处理任务（统一处理文件）
        future = executor.submit(
            run_async_task,
            process_file_async,
            filepath,
            task_id
        )
        
        processing_tasks[task_id] = {
            'future': future,
            'progress': 0.0,
            'filepath': filepath,
            'start_time': time.time(),
            'display_options': display_options  # 新增显示选项存储
        }
        
        return jsonify({
            "status": "processing",
            "task_id": task_id
        }), 202

    except Exception as e:
        app.logger.error(f"处理请求失败: {str(e)}")
        return jsonify({"error": f"サーバーエラー: {str(e)}"}), 500



@app.route('/status/<task_id>')
def analysis_status(task_id):
    task = processing_tasks.get(task_id)
    if not task:
        return jsonify({
            "status": "error",
            "message": "無効なタスクID"
        }), 404

    # 添加超时检测
    if time.time() - task['start_time'] > 1800:  # 30分钟超时
        return jsonify({
            "status": "error",
            "message": "処理タイムアウト"
        })

    if task['future'].done():
        try:
            result_path = task['future'].result()
            return jsonify({
                "status": "completed",
                "redirect": url_for('show_result', task_id=task_id)
            })
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"結果生成失敗: {str(e)}"
            })
    else:
        return jsonify({
            "status": "processing",
            "progress": task['progress']
        })


@app.route('/result/<task_id>')
def show_result(task_id):
    """显示最终结果"""
    result_path = f"uploads/{task_id}_result.json"
    if not os.path.exists(result_path):
        return render_template('error.html', message="结果不存在")
    
    with open(result_path) as f:
        data = json.load(f)

    task = processing_tasks.get(task_id, {})
    display_options = task.get('display_options', [])
    
    analysis = analyze_data(data['results'])
    visualizations = create_visualizations(analysis)
    ai_summary = generate_summary(analysis, data['results'])

    source_type = 'youtube' if '_youtube' in task_id else \
                 'file' if '_file' in task_id else 'text'
    
    return render_template(
        'dashboard.html',
        source_type=source_type,
        analysis=analysis,
        charts=visualizations,
        total=analysis['total'],
        comments=data['results'][:100],
        ai_summary=ai_summary,
        display_options=display_options,
    )


# app.py 路由修正部分
@app.route('/', methods=['GET'])
def index():
    return render_template('upload.html')

@app.after_request
def add_header(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(host='0.0.0.0', port=80, threaded=True)
