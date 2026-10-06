# ---------- analysis.py ----------
"""共有の感情分析ロジック（プロンプト・API呼び出し・JSON解析・集計）。

app.async.py と testapi.py の両方から利用される。
"""
import asyncio
import json
import logging
import os
import re
import time
import traceback
from collections import defaultdict

import aiohttp
from openai import OpenAI

logger = logging.getLogger(__name__)

ARK_API_KEY = os.getenv('ARK_API_KEY', 'your-api-key')
ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MODEL_NAME = "deepseek-v3-241226"

client = OpenAI(api_key=ARK_API_KEY, base_url=ARK_BASE_URL)

VALID_SENTIMENTS = ("ポジティブ", "中立", "ネガティブ")
VALID_POLITENESS = ("砕けた", "普通", "丁寧")

# Ark は OpenAI 互換の response_format（json_schema / strict）に対応しているモデルが
# 多いため、スキーマで構造を強制する。これにより旧版にあった
# 「直接parse→正規表現で抽出→文字列置換で無理やり直す」という3段フォールバックが不要になる。
ANALYSIS_JSON_SCHEMA = {
    "name": "comment_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "要約": {
                "type": "string",
                "description": "コメントの核心的事実を20文字以内・動詞終わりで要約する",
            },
            "基本感情": {"type": "string", "enum": list(VALID_SENTIMENTS)},
            "感情詳細": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
                "description": "感情を表す短いタグ（最大3個）",
            },
            "核心視点": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 1,
                "description": "コメントから抽出した最も代表的な観点キーワード（2〜8文字、流行語や抽象語は避ける）",
            },
            "顔文字影響": {"type": "integer", "minimum": 0, "maximum": 3},
            "潜在意図": {"type": "integer", "minimum": 0, "maximum": 20},
            "礼儀レベル": {"type": "string", "enum": list(VALID_POLITENESS)},
        },
        "required": ["要約", "基本感情", "感情詳細", "核心視点", "顔文字影響", "潜在意図", "礼儀レベル"],
        "additionalProperties": False,
    },
}

ANALYSIS_PROMPT = """以下の日本語YouTubeコメントを分析してください。

▼ 分析のルール
1. 要約: 意見や感情表現を除いた事実のみを、動詞で終わる形で20文字以内にまとめる（例:「カメラワークを指摘」）
2. 核心視点: コメントの立場・感情・要望を最もよく表す1個のキーワードを抽出する
   - 名詞または動詞の基本形を使う
   - 2〜8文字程度
   - 流行語やあいまいな抽象語は避ける
3. 感情詳細: 感情を表す短いタグを最大3個（例:「喜び」「称賛」「失望」）
4. 顔文字影響・潜在意図は、コメント中の顔文字・ネットスラング・言い回しからの推定でよい

分析対象コメント:
{content}"""


def parse_ai_json(content):
    """AIの応答テキストから分析JSONを取り出す。

    schema で構造を強制しているため基本は直接 json.loads で読める想定だが、
    モデルやAPI側の都合でプレーンな json_object 応答に落ちた場合に備えて
    コードブロックで包まれたJSONの抽出だけ最低限フォローする。

    戻り値: (parsed_dict または None, 使用したモード名 または None)
    """
    try:
        return json.loads(content), "直接JSON"
    except json.JSONDecodeError:
        pass

    json_match = re.search(r'({.*})', content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1)), "包裹JSON"
        except Exception as e:
            logger.debug(f"包裹解析失败: {e}")

    return None, None


def _fallback_analysis():
    return {
        "基本感情": "中立",
        "感情詳細": ["解析失败"],
        "核心視点": [],
        "顔文字影響": 0,
        "潜在意図": 0,
        "礼儀レベル": "普通",
        "__error": True,
    }


async def async_analyze(content):
    """1件のコメントを非同期でAI分析する。失敗時は中立のフォールバックを返す。"""
    messages = [{
        "role": "user",
        "content": ANALYSIS_PROMPT.format(content=content)
    }]

    async with aiohttp.ClientSession() as session:
        for attempt in range(3):
            start_time = time.time()
            raw_content = None
            try:
                async with session.post(
                    ARK_API_URL,
                    json={
                        "model": MODEL_NAME,
                        "messages": messages,
                        "temperature": 0.3,
                        "max_tokens": 1024,
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": ANALYSIS_JSON_SCHEMA,
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {ARK_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"HTTP错误 {response.status}: {error_text[:200]}...")
                        continue

                    data = await response.json()
                    raw_content = data['choices'][0]['message']['content']

                    analysis, parse_type = parse_ai_json(raw_content)
                    if not analysis:
                        raise ValueError("JSON解析に失敗")

                    if analysis.get("基本感情") not in VALID_SENTIMENTS:
                        raise ValueError(f"无效情感值：{analysis.get('基本感情')}")

                    logger.debug(f"解析成功（模式：{parse_type}）")

                    return {
                        "要約": str(analysis.get("要約", "要約生成失敗")),
                        "基本感情": str(analysis["基本感情"]),
                        "感情詳細": list(map(str, analysis.get("感情詳細", []))),
                        "核心視点": list(map(str, analysis.get("核心視点", []))),
                        "顔文字影響": int(analysis.get("顔文字影響", 0)),
                        "潜在意図": int(analysis.get("潜在意図", 0)),
                        "礼儀レベル": str(analysis.get("礼儀レベル", "普通")),
                    }

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                wait_time = 2 ** attempt
                logger.warning(f"网络错误 ({type(e).__name__})，{wait_time}s后重试...")
                await asyncio.sleep(wait_time)

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.error(f"解析失败 [{type(e).__name__}]: {e}")
                if attempt == 2 and raw_content:
                    logger.debug(f"原始响应内容：{raw_content[:200]}...")

            finally:
                elapsed = time.time() - start_time
                await asyncio.sleep(max(0.0, 0.2 - elapsed))

    return _fallback_analysis()


def validate_youtube_url(url):
    regex = r'^(https?\:\/\/)?(www\.)?(youtube\.com|youtu\.?be)\/.+'
    return re.match(regex, url) is not None


def extract_video_id(url):
    reg_exp = r'^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*'
    match = re.match(reg_exp, url)
    return match.group(2) if match and len(match.group(2)) == 11 else None


def analyze_data(data):
    """コメントごとの分析結果リストを集計する。"""
    analysis = defaultdict(lambda: defaultdict(int))
    analysis['total'] = len(data)

    if analysis['total'] == 0:
        return {
            'total': 0,
            'sentiment_counts': {'ポジティブ': 0, '中立': 0, 'ネガティブ': 0},
            'emotion_freq': defaultdict(int),
            'viewpoint_freq': defaultdict(int),
        }

    for entry in data:
        entry_analysis = entry['analysis']
        analysis['sentiment_counts'][entry_analysis.get('基本感情', '')] += 1

        for emotion in entry_analysis.get('感情詳細', []):
            if emotion and str(emotion).strip():
                analysis['emotion_freq'][str(emotion).strip()[:20]] += 1

        for keyword in entry_analysis.get('核心視点', []):
            if keyword and str(keyword).strip():
                analysis['viewpoint_freq'][str(keyword).strip()[:20]] += 1

    if not analysis['sentiment_counts']:
        analysis['sentiment_counts'] = {'ポジティブ': 0, '中立': 0, 'ネガティブ': 0}

    if not analysis['emotion_freq']:
        analysis['emotion_freq']['（データなし）'] = 0

    return analysis


def create_visualizations(analysis):
    """観点ワードクラウド用データを生成する。"""
    viewpoint_items = sorted(
        analysis['viewpoint_freq'].items(),
        key=lambda x: -x[1]
    )[:100]

    return {
        'viewpoint_data': [{'text': k, 'value': v} for k, v in viewpoint_items]
    }


def generate_summary(analysis, raw_data):
    """生コメントからAIレポートを生成する。"""
    try:
        raw_comments = []
        try:
            raw_comments = [
                str(entry.get('content', '')).strip()
                for entry in raw_data[:100]
                if entry.get('content')
            ]
            raw_comments = [
                re.sub(r'[\x00-\x1F\x7F-\x9F]', '', c)
                for c in raw_comments
                if c and len(c) >= 2
            ][:100]
        except Exception as e:
            logger.error(f"原始评论提取失败: {e}")
            traceback.print_exc()

        if not raw_comments:
            return "⚠️ 分析対象のコメントが見つかりません\n考えられる原因:\n• コメント抽出エラー\n• 文字コード問題"

        comment_list = []
        for idx, comment in enumerate(raw_comments, 1):
            trimmed = (comment[:120] + '...') if len(comment) > 120 else comment
            comment_list.append(f"{idx:03d}. {trimmed}")
        comment_text = "\n".join(comment_list)

        non_jp_chars = sum(1 for c in ''.join(raw_comments) if ord(c) > 255)
        lang_note = ""
        if non_jp_chars / len(''.join(raw_comments)) > 0.3:
            lang_note = "※英語コメントが含まれている場合は適宜翻訳してください\n"

        summary_prompt = f"""📊 コメント分析レポート生成指示
以下の実際のコメント(100件)を精読し、以下の要素を含むレポートを生成（纯文字格式、markdown禁止）：

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

{lang_note}▼ 分析対象コメント:
{comment_text}

▼ 分析結果："""

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": summary_prompt}],
                    temperature=0.5,
                    max_tokens=800,
                    timeout=30
                )

                summary = response.choices[0].message.content.strip()
                cleaned_summary = re.sub(
                    r'(\n{2,})', '\n',
                    summary.replace('※', '')
                ).strip()
                return cleaned_summary

            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait_time = 2 ** attempt
                logger.warning(f"API调用失败（{e}），{wait_time}s后重试...")
                time.sleep(wait_time)

    except Exception as e:
        logger.error(f"总结生成失败: {traceback.format_exc()}")
        return f"""⚠️ レポート生成エラー
【原因】{e}
【対処法】
1. コメントを短く分割
2. 再試行してください
3. 問題が続く場合は管理者へ連絡"""

    return "レポート生成に失敗しました。入力データの形式をご確認ください"
