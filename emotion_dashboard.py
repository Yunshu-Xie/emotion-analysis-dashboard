# 可视化代码 emotion_dashboard.py
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import matplotlib.pyplot as plt
from wordcloud import WordCloud
import json
import pandas as pd

# 配置日语字体（需提前安装IPAex字体）
plt.rcParams['font.sans-serif'] = ['IPAexGothic']
plt.rcParams['axes.unicode_minus'] = False

# 示例数据
sample_data = {
    "原文": "この企画マジ卍！って感じですわ～(笑)",
    "分析": {
        "基本感情": "ポジティブ",
        "感情詳細": ["喜び", "興奮"],
        "ネットスラング": ["卍"],
        "顔文字影響": 2,
        "潜在意図": 15,
        "礼儀レベル": "砕けた",
        "方言": "関西弁",
        "文化参照": "若者言葉"
    }
}

def create_emotion_radar(data):
    """创建情感雷达图"""
    categories = ['喜び', '怒り', '悲しみ', '期待', '驚き']
    values = [0.8 if cat in data['感情詳細'] else 0.2 for cat in categories]
    
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values,
        theta=categories,
        fill='toself',
        name='情感强度',
        line_color='gold'
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 1]
            )),
        showlegend=False,
        title='情感分布雷达图'
    )
    return fig

def create_cultural_wordcloud(data):
    """创建文化特征词云"""
    cultural_text = ' '.join([
        data['方言'], 
        data['文化参照'],
        *data['ネットスラング']
    ]*5)
    
    wordcloud = WordCloud(
        width=800,
        height=400,
        background_color='white',
        font_path='ipaexg.ttf'  # 需确保字体存在
    ).generate(cultural_text)
    
    fig, ax = plt.subplots()
    ax.imshow(wordcloud, interpolation='bilinear')
    ax.axis("off")
    return fig

def main():
    st.set_page_config(page_title="日语文本分析仪", layout="wide")
    
    # 侧边栏
    with st.sidebar:
        st.header("分析参数")
        st.code(json.dumps(sample_data['分析'], indent=2, ensure_ascii=False))
    
    # 主界面
    col1, col2 = st.columns([3, 2])
    
    with col1:
        st.subheader("原文内容")
        st.markdown(f"```\n{sample_data['原文']}\n```")
        
        # 情感指标仪表盘
        st.subheader("情感指标")
        cols = st.columns(3)
        with cols[0]:
            st.metric("基本感情", sample_data['分析']['基本感情'])
        with cols[1]:
            st.metric("礼儀レベル", sample_data['分析']['礼儀レベル'])
        with cols[2]:
            st.metric("潜在意図", f"{sample_data['分析']['潜在意図']}/20")
        
        # 情感雷达图
        st.plotly_chart(create_emotion_radar(sample_data['分析']), use_container_width=True)
    
    with col2:
        # 文化特征词云
        st.subheader("文化特征可视化")
        cultural_fig = create_cultural_wordcloud(sample_data['分析'])
        st.pyplot(cultural_fig)
        
        # 顔文字影响可视化
        st.subheader("顔文字影响")
        emoji_effect = sample_data['分析']['顔文字影響']
        st.progress(emoji_effect/5, text=f"影响强度: {'★'*emoji_effect}{'☆'*(5-emoji_effect)}")
        
        # 方言分布图
        dialect_df = pd.DataFrame({
            "方言": ["関西弁", "標準語", "東北弁"],
            "出现率": [65, 30, 5]
        })
        fig = px.pie(dialect_df, names='方言', values='出现率', 
                    title="方言分布预测", hole=0.3)
        st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()