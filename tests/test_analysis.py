import json

from analysis import (analyze_data, create_visualizations, extract_video_id,
                       parse_ai_json, validate_youtube_url)


def test_parse_ai_json_direct():
    parsed, mode = parse_ai_json('{"基本感情": "ポジティブ"}')
    assert parsed == {"基本感情": "ポジティブ"}
    assert mode == "直接JSON"


def test_parse_ai_json_wrapped_in_text():
    content = 'ここに説明文があります\n{"基本感情": "中立"}\nおまけのテキスト'
    parsed, mode = parse_ai_json(content)
    assert parsed == {"基本感情": "中立"}
    assert mode == "包裹JSON"


def test_parse_ai_json_invalid_returns_none():
    parsed, mode = parse_ai_json('これはJSONではありません')
    assert parsed is None
    assert mode is None


def test_validate_youtube_url_accepts_standard_url():
    assert validate_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_validate_youtube_url_accepts_short_url():
    assert validate_youtube_url("https://youtu.be/dQw4w9WgXcQ")


def test_validate_youtube_url_rejects_other_domains():
    assert not validate_youtube_url("https://example.com/watch?v=dQw4w9WgXcQ")


def test_extract_video_id_from_watch_url():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_from_short_url():
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_returns_none_for_invalid_id_length():
    assert extract_video_id("https://www.youtube.com/watch?v=short") is None


def _entry(sentiment, emotions=None, keywords=None):
    return {
        "content": "テストコメント",
        "analysis": {
            "基本感情": sentiment,
            "感情詳細": emotions or [],
            "核心視点": keywords or [],
        },
    }


def test_analyze_data_empty_list():
    result = analyze_data([])
    assert result['total'] == 0
    assert result['sentiment_counts'] == {'ポジティブ': 0, '中立': 0, 'ネガティブ': 0}


def test_analyze_data_counts_sentiment_and_keywords():
    data = [
        _entry("ポジティブ", ["喜び"], ["衣装"]),
        _entry("ポジティブ", ["称賛"], ["衣装"]),
        _entry("ネガティブ", ["失望"], ["編集"]),
    ]
    result = analyze_data(data)

    assert result['total'] == 3
    assert result['sentiment_counts']['ポジティブ'] == 2
    assert result['sentiment_counts']['ネガティブ'] == 1
    assert result['emotion_freq']['喜び'] == 1
    assert result['viewpoint_freq']['衣装'] == 2
    assert result['viewpoint_freq']['編集'] == 1


def test_create_visualizations_sorts_by_frequency_desc():
    data = [
        _entry("ポジティブ", keywords=["A"]),
        _entry("ポジティブ", keywords=["A"]),
        _entry("ポジティブ", keywords=["B"]),
    ]
    analysis_result = analyze_data(data)
    viz = create_visualizations(analysis_result)

    assert viz['viewpoint_data'][0] == {'text': 'A', 'value': 2}
    assert viz['viewpoint_data'][1] == {'text': 'B', 'value': 1}
