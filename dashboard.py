import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import time
from pathlib import Path
import numpy as np

# Настройки страницы
st.set_page_config(
    page_title="PREDATOR v2.0 Dashboard",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Пути к файлам
BASE_DIR = Path(__file__).parent
METRICS_CSV = BASE_DIR / "logs" / "gwdd_metrics.csv"
TRADE_HISTORY_CSV = BASE_DIR / "trade_history.csv"

# Кастомный CSS
st.markdown("""
<style>
    .main {background-color: #0e1117;}
    .stMetric {background-color: #1e2130; padding: 15px; border-radius: 10px;}
    h1 {color: #2196F3;}
    h2 {color: #4CAF50;}
    h3 {color: #ff9800;}
</style>
""", unsafe_allow_html=True)

# Заголовок
st.title("🚀 PREDATOR v2.0 GWDD Dashboard")
st.markdown("**Real-time Natural Gas Trading System | GWDD Neuro-Engine Active**")

# Автообновление
auto_refresh = st.sidebar.checkbox("🔄 Auto-refresh (5s)", value=True)
if auto_refresh:
    time.sleep(5)
    st.rerun()

# Функция загрузки метрик
@st.cache_data(ttl=5)
def load_metrics():
    try:
        if not METRICS_CSV.exists():
            return pd.DataFrame()
        df = pd.read_csv(METRICS_CSV)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
    except Exception as e:
        st.error(f"Ошибка загрузки метрик: {e}")
        return pd.DataFrame()

# Функция загрузки истории сделок
@st.cache_data(ttl=5)
def load_trades():
    try:
        if not TRADE_HISTORY_CSV.exists():
            return pd.DataFrame()
        df = pd.read_csv(TRADE_HISTORY_CSV)
        df['time'] = pd.to_datetime(df['time'])
        return df
    except Exception as e:
        return pd.DataFrame()

# Загрузка данных
metrics_df = load_metrics()
trades_df = load_trades()

# Sidebar - Фильтры
st.sidebar.header("⚙️ Настройки")
time_range = st.sidebar.selectbox(
    "Временной диапазон",
    ["Последний час", "Последние 4 часа", "24 часа", "Всё время"]
)

# Фильтрация по времени
if not metrics_df.empty:
    now = datetime.now()
    if time_range == "Последний час":
        metrics_df = metrics_df[metrics_df['timestamp'] > now - timedelta(hours=1)]
    elif time_range == "Последние 4 часа":
        metrics_df = metrics_df[metrics_df['timestamp'] > now - timedelta(hours=4)]
    elif time_range == "24 часа":
        metrics_df = metrics_df[metrics_df['timestamp'] > now - timedelta(hours=24)]

# Основные метрики
if not metrics_df.empty and not trades_df.empty:
    col1, col2, col3, col4, col5 = st.columns(5)
    
    # PnL расчет
    if 'lots_after' in trades_df.columns and 'price' in trades_df.columns:
        total_trades = len(trades_df)
        wins = len(trades_df[trades_df['lots_after'] == 0])  # Закрытие позиции
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Примерный PnL (упрощённый расчёт)
        last_price = metrics_df['price'].iloc[-1] if 'price' in metrics_df else 0
        
        col1.metric("📈 Total Trades", total_trades)
        col2.metric("✅ Win Rate", f"{win_rate:.1f}%")
        col3.metric("💰 Last Price", f"${last_price:.4f}")
        
    # GWDD метрики
    if 'gwdd_weight' in metrics_df.columns:
        avg_gwdd = metrics_df['gwdd_weight'].mean()
        col4.metric("🌡️ Avg GWDD Weight", f"{avg_gwdd:.2f}")
        
    if 'ai_conf' in metrics_df.columns:
        avg_conf = metrics_df['ai_conf'].mean()
        col5.metric("🎯 Avg AI Confidence", f"{avg_conf:.1f}%")

    st.divider()

    # Графики
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Метрики", "💹 Цена & GWDD", "🎯 Сигналы", "📋 История"])

    with tab1:
        col1, col2 = st.columns(2)
        
        with col1:
            # График RSI
            if 'rsi' in metrics_df.columns:
                fig_rsi = go.Figure()
                fig_rsi.add_trace(go.Scatter(
                    x=metrics_df['timestamp'],
                    y=metrics_df['rsi'],
                    mode='lines',
                    name='RSI',
                    line=dict(color='#2196F3', width=2)
                ))
                fig_rsi.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="Overbought")
                fig_rsi.add_hline(y=30, line_dash="dash", line_color="green", annotation_text="Oversold")
                fig_rsi.update_layout(
                    title="RSI Indicator",
                    xaxis_title="Time",
                    yaxis_title="RSI",
                    height=400,
                    template="plotly_dark"
                )
                st.plotly_chart(fig_rsi, use_container_width=True)
        
        with col2:
            # График AI Confidence
            if 'ai_conf' in metrics_df.columns:
                fig_conf = go.Figure()
                fig_conf.add_trace(go.Scatter(
                    x=metrics_df['timestamp'],
                    y=metrics_df['ai_conf'],
                    mode='lines+markers',
                    name='AI Confidence',
                    line=dict(color='#4CAF50', width=2),
                    marker=dict(size=6)
                ))
                fig_conf.update_layout(
                    title="AI Confidence Over Time",
                    xaxis_title="Time",
                    yaxis_title="Confidence %",
                    height=400,
                    template="plotly_dark"
                )
                st.plotly_chart(fig_conf, use_container_width=True)

    with tab2:
        # Price & GWDD Weight
        if 'price' in metrics_df.columns and 'gwdd_weight' in metrics_df.columns:
            fig = go.Figure()
            
            # Цена на первой оси
            fig.add_trace(go.Scatter(
                x=metrics_df['timestamp'],
                y=metrics_df['price'],
                name='Price',
                yaxis='y1',
                line=dict(color='#2196F3', width=3)
            ))
            
            # GWDD Weight на второй оси
            fig.add_trace(go.Bar(
                x=metrics_df['timestamp'],
                y=metrics_df['gwdd_weight'],
                name='GWDD Weight',
                yaxis='y2',
                marker=dict(color='#ff9800', opacity=0.6)
            ))
            
            fig.update_layout(
                title="Price vs GWDD Weight Correlation",
                xaxis=dict(title="Time"),
                yaxis=dict(title="Price ($)", side="left"),
                yaxis2=dict(title="GWDD Weight", side="right", overlaying="y"),
                height=500,
                template="plotly_dark",
                hovermode='x unified'
            )
            st.plotly_chart(fig, use_container_width=True)

    with tab3:
        # Bull vs Bear Probability
        if 'bull_prob' in metrics_df.columns and 'bear_prob' in metrics_df.columns:
            fig = go.Figure()
            
            fig.add_trace(go.Scatter(
                x=metrics_df['timestamp'],
                y=metrics_df['bull_prob'],
                name='Bull Probability',
                fill='tonexty',
                line=dict(color='#4CAF50', width=2)
            ))
            
            fig.add_trace(go.Scatter(
                x=metrics_df['timestamp'],
                y=metrics_df['bear_prob'],
                name='Bear Probability',
                fill='tozeroy',
                line=dict(color='#f44336', width=2)
            ))
            
            fig.update_layout(
                title="Bull vs Bear Probability",
                xaxis_title="Time",
                yaxis_title="Probability",
                height=500,
                template="plotly_dark",
                hovermode='x unified'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        # AI Signal Distribution
        if 'ai_signal' in metrics_df.columns:
            signal_counts = metrics_df['ai_signal'].value_counts()
            fig_pie = px.pie(
                values=signal_counts.values,
                names=signal_counts.index,
                title="AI Signal Distribution",
                color_discrete_sequence=['#4CAF50', '#f44336', '#ff9800']
            )
            fig_pie.update_layout(template="plotly_dark", height=400)
            st.plotly_chart(fig_pie, use_container_width=True)

    with tab4:
        # История сделок
        st.subheader("📋 Trade History")
        if not trades_df.empty:
            st.dataframe(
                trades_df[['time', 'action', 'lots_before', 'lots_after', 'price', 'signal', 'confidence']].tail(50),
                use_container_width=True,
                height=400
            )
        else:
            st.info("Нет данных о сделках")
            
        # Последние метрики
        st.subheader("🔢 Recent Metrics")
        if not metrics_df.empty:
            st.dataframe(
                metrics_df[['timestamp', 'cycle', 'price', 'rsi', 'ai_signal', 'ai_conf', 'gwdd_weight']].tail(20),
                use_container_width=True,
                height=400
            )

else:
    st.warning("⚠️ Нет данных для отображения. Проверьте что бот запущен и пишет в gwdd_metrics.csv")
    st.info(f"Ожидаемый путь к метрикам: {METRICS_CSV}")

# Футер
st.divider()
st.markdown("""
<div style='text-align: center; color: #666; font-size: 12px;'>
    🤖 PREDATOR v2.0 GWDD Neuro-Engine | Auto-refresh: 5s | 
    Last update: """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """
</div>
""", unsafe_allow_html=True)
