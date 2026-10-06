# test.py

import json
import re
import requests

def test_api_response():
    """增强版API响应验证脚本"""
    config = {
        "api_key": "xxxx",
        "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": "deepseek-v3-241226"
    }

    # 强化Prompt设计
    test_payload = {
        "model": config["model"],
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
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(config["api_url"], headers=headers, json=test_payload, timeout=15)
        response.raise_for_status()

        data = response.json()
        content = data['choices'][0]['message']['content']
        print(f"原始响应内容类型：{type(content)}")
        print(f"响应内容样例：{content[:200]}")

        # 多模式解析逻辑
        def parse_content(content):
            # 模式1：直接解析
            try:
                return json.loads(content), "直接JSON"
            except json.JSONDecodeError:
                pass

            # 模式2：提取被包裹的JSON
            json_match = re.search(r'({.*})', content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1)), "包裹JSON"
                except Exception as e:
                    print(f"包裹JSON解析失败：{str(e)}")

            # 模式3：容错解析
            try:
                sanitized = content.replace("'", '"').replace("\n", "")
                return json.loads(sanitized), "修正后JSON"
            except Exception as e:
                print(f"容错解析失败：{str(e)}")
                return None

        parsed_data, parse_type = parse_content(content)
        
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