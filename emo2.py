# ---------- app.py ----------
import os
import json
import time
from flask import Flask, render_template, request, redirect, url_for, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import pandas as pd
import plotly.express as px
from collections import defaultdict
from openai import OpenAI
import pygal

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB限制
app.config['ARK_API_KEY'] = os.getenv('ARK_API_KEY', 'your-api-key')
app.config['MAX_WORKERS'] = 5  # 并行请求数

# 初始化OpenAI客户端
client = OpenAI(
    api_key=app.config['ARK_API_KEY'],
    base_url="https://ark.cn-beijing.volces.com/api/v3"
)

# 情感分析prompt模板（日语）
ANALYSIS_PROMPT = """请对以下日本YouTube评论进行详细情感分析，使用JSON格式返回结果，不添加注释，不使用markdown，包含以下字段：
1. 基本感情（ポジティブ/中立/ネガティブ）
2. 感情詳細（最多3个情感标签）
3. ネットスラング（列表）
4. 顔文字影響（0-3等级）
5. 潜在意図（0-20得分）
6. 礼儀レベル（砕けた/普通/丁寧）
7. 方言（检测到的方言）
8. 文化参照（关联的日本文化元素）

请特别注意：
- 网络用语识别（如wwww、卍、尊い）
- 颜文字分析（如(^_^;)、ﾄﾎﾎ...）
- 日本特有的文化引用（动漫、偶像文化等）

评论内容：{content}

返回格式：
{{
  "分析": {{
    "基本感情": "",
    "感情詳細": [],
    "ネットスラング": [],
    "顔文字影響": 0,
    "潜在意図": 0,
    "礼儀レベル": "",
    "方言": "",
    "文化参照": ""
  }}
}}"""

# 在ANALYSIS_PROMPT后添加总结prompt
SUMMARY_PROMPT = """📊 YouTubeコメント分析レポート生成のお願い
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
- 100文字以内
- 絵文字を適度に使用（例: 👍）
- 見出し記号「▶」使用

実際の分析結果："""

def generate_summary(analysis, raw_data):
    """生成总结报告"""
    # 准备数据
    sentiment_dist = {
        k: f"{(v/analysis['total']*100):.1f}%" 
        for k, v in analysis['sentiment_counts'].items()
    }
    
    top_emotions = sorted(
        analysis['emotion_freq'].items(),
        key=lambda x: x[1],
        reverse=True
    )[:5]
    
    cultural_refs = list({
        entry['analysis'].get('文化参照', '') 
        for entry in raw_data 
        if entry['analysis'].get('文化参照')
    })
    
    # 构建prompt
    prompt = SUMMARY_PROMPT.format(
        total=analysis['total'],
        sentiment_distribution=", ".join([f"{k} {v}" for k,v in sentiment_dist.items()]),
        top_emotions=", ".join([f"{k}({v}回)" for k,v in top_emotions]),
        top_slangs=", ".join([f"{k}({v}回)" for k,v in analysis['slang_freq'].items()][:5]),
        dialects=", ".join(list({
            entry['analysis'].get('方言', '') 
            for entry in raw_data 
            if entry['analysis'].get('方言')
        })),
        cultural_refs=", ".join(cultural_refs[:5])
    )
    
    # 调用API
    #headers = {
    #    "Authorization": f"Bearer {app.config['DASHSCOPE_API_KEY']}",
    #    "Content-Type": "application/json"
    # }
    
    payload = {
        "model": "deepseek-v3-241226",
        "messages": [{
            "role": "user",
            "content": prompt
        }],
        "temperature": 0.7,
        "max_tokens": 1024
    }
    # 调用API（增强版）
    max_retries = 3
    timeout_seconds = 60  # 延长超时时间
    backoff_base = 1.5    # 退避基数
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="deepseek-v3-241226",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=800,  # 减少生成长度
                timeout=timeout_seconds
            )

            # 删除开头的所有换行符
            cleaned_summary = response.choices[0].message.content.strip()
            cleaned_summary = re.sub(r'^\n+', '', cleaned_summary)  # 删除开头的所有换行符
            return cleaned_summary

        except APITimeoutError as e:  # 捕获具体超时异常
            print(f"API超时（第{attempt+1}次尝试）")
            if attempt < max_retries - 1:
                wait_time = backoff_base ** attempt
                print(f"等待{wait_time:.1f}秒后重试...")
                time.sleep(wait_time)
        except Exception as e:
            print(f"API错误: {str(e)}")
            break
    
    # 最终失败处理
    error_msg = f"要約生成失敗（{max_retries}回試行）\n考えられる原因：\n• サーバー混雑\n• 長文処理時間不足\n【代替提案】\n1. ファイルを分割して再試行\n2. ネットワーク接続を確認\n3. 後ほど再度お試しください"
    return error_msg



def load_data(filepath):
    data = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if '分析' in entry and '感情詳細' in entry['分析']:
                    data.append(entry)
            except json.JSONDecodeError:
                continue
    return data

def analyze_with_deepseek(content):
    """修改后的API调用函数"""
    # 请求参数调整
    messages = [{
        "role": "user",
        "content": ANALYSIS_PROMPT.format(content=content)
    }]

    # 智能重试配置（保持原有逻辑）
    retries = 3
    backoff_base = 2
    timeout_config = 30  # 统一超时时间

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="deepseek-v3-241226",  # 修改模型名称
                messages=messages,
                temperature=0.3,
                max_tokens=1024,
                top_p=0.8,
                timeout=timeout_config
            )
            
            # 响应解析调整
            json_str = re.search(r'\{[\s\S]*\}', response.choices[0].message.content).group()
            return json.loads(json_str)

        except Exception as e:
            print(f"API请求异常: {str(e)}")
            if attempt == retries - 1:
                raise Exception(f"API请求失败（重试{retries}次）")
            wait_time = backoff_base ** attempt
            print(f"重试 #{attempt+1}, 等待 {wait_time}秒...")
            time.sleep(wait_time)

def process_file(filepath):
    """并行处理文件"""
    results = []
    failed_lines = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        contents = [line.strip() for line in f if line.strip()]
    
    # 速率限制控制（每秒5个请求）
    RATE_LIMIT = 5
    delay = 1.0 / RATE_LIMIT
    
    with ThreadPoolExecutor(max_workers=app.config['MAX_WORKERS']) as executor:
        futures = {}
        start_time = time.time()
        
        for idx, content in enumerate(contents):
            # 控制请求速率
            if idx % RATE_LIMIT == 0 and idx != 0:
                elapsed = time.time() - start_time
                sleep_time = max(0, delay - elapsed)
                time.sleep(sleep_time)
                start_time = time.time()
            
            future = executor.submit(analyze_with_deepseek, content)
            futures[future] = content
        
        for future in as_completed(futures):
            content = futures[future]
            try:
                result = future.result()
                if result and '分析' in result:
                    results.append({
                        "content": content,
                        "analysis": result['分析']
                    })
                else:
                    failed_lines.append(content)
            except Exception as e:
                print(f"分析失败: {str(e)}")
                failed_lines.append(content)
    
    print(f"成功分析 {len(results)} 条，失败 {len(failed_lines)} 条")
    return results


def analyze_data(data):
    analysis = defaultdict(lambda: defaultdict(int))

    for entry in data:
    # 情感详细处理增强
        emotions = entry['analysis'].get('感情詳細', [])
        for emotion in emotions:
            if emotion and str(emotion).strip():  # 过滤空值
                clean_emotion = str(emotion).strip()[:20]  # 限制长度
                analysis['emotion_freq'][clean_emotion] += 1
            
    
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
    """生成词云数据"""
    # 提取网络用语数据并按频率排序
    slang_items = sorted(
        analysis['slang_freq'].items(),
        key=lambda x: -x[1]
    )[:100]  # 取前100个高频词
    
    # 转换为词云需要的格式
    slang_data = [{'text': k, 'value': v} for k, v in slang_items]
    
    return {
        'slang_data': slang_data
    }


@app.after_request
def add_header(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # 获取输入内容
        input_type = request.form.get('input_type', 'file')
        file = request.files.get('file')
        text_content = request.form.get('text', '')


        
        # 验证输入
        if input_type == 'file' and not file:
            return render_template('error.html', message="ファイルを選択してください")
        if input_type == 'text' and not text_content.strip():
            return render_template('error.html', message="テキストを入力してください")
        
        # 处理输入
        raw_data = []
        try:
            if input_type == 'file':
                filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
                file.save(filename)
                try:
                    raw_data = process_file(filename)
                finally:
                    # 确保文件删除（即使处理过程中出现异常）
                    if os.path.exists(filename):
                        os.remove(filename)
                        print(f"已删除上传文件: {filename}")
            else:
                # 处理文本输入
                contents = [line.strip() for line in text_content.split('\n') if line.strip()]
                with ThreadPoolExecutor(max_workers=app.config['MAX_WORKERS']) as executor:
                    futures = {executor.submit(analyze_with_deepseek, content): content for content in contents}
                    raw_data = []
                    for future in as_completed(futures):
                        content = futures[future]
                        try:
                            result = future.result()
                            if result and '分析' in result:
                                raw_data.append({
                                    "content": content,
                                    "analysis": result['分析']
                                })
                        except Exception as e:
                            print(f"分析失败: {str(e)}")
            
            if not raw_data:
                return render_template('error.html', message="分析結果がありません")

            display_options = request.form.getlist('display_options')
            
            analysis = analyze_data(raw_data)
            visualizations = create_visualizations(analysis)
            ai_summary = generate_summary(analysis, raw_data)
            
            return render_template(
                'dashboard.html',
                analysis=analysis,
                charts=visualizations,
                total=analysis['total'],
                comments=raw_data[:100],
                ai_summary=ai_summary,  # 新增参数
                display_options=display_options,
            )
            
        except Exception as e:
            return render_template('error.html', message=f"処理エラー: {str(e)}")

        return handle_analysis()
    
    return render_template('upload.html')

@app.route('/error')
def show_error():
    return render_template('error.html', message="ページが見つかりません")


if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(host='0.0.0.0', port=4399)
