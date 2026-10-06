# test.py
import json

import requests

from analysis import ARK_API_URL, MODEL_NAME, parse_ai_json


def test_api_response():
    """增强版API响应验证脚本"""
    api_key = "xxxx"  # 替换为真实API key，或通过 ARK_API_KEY 环境变量读取

    test_payload = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": """严格按以下JSON格式响应：
{
  "分析": {
    "基本感情": "選択肢",
    "感情詳細": ["标签"],
    "ネットスラング": ["用語"],
    "顔文字影響": 数字
  }
}
请分析以下内容：
テスト用コメント"""
        }],
        "temperature": 0.3,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"}
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(ARK_API_URL, headers=headers, json=test_payload, timeout=15)
        response.raise_for_status()

        data = response.json()
        content = data['choices'][0]['message']['content']
        print(f"原始响应内容类型：{type(content)}")
        print(f"响应内容样例：{content[:200]}")

        parsed_data, parse_type = parse_ai_json(content)

        if parsed_data:
            print(f"✅ 解析成功 ({parse_type})")
            print(json.dumps(parsed_data, indent=2, ensure_ascii=False))
            return True
        else:
            print("❌ 所有解析方式均失败")
            return False

    except Exception as e:
        print(f"❌ 测试失败：{str(e)}")
        return False


if __name__ == "__main__":
    print("\n=== API响应结构深度测试 ===")
    test_api_response()
