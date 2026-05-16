@echo off
chcp 65001 >nul
echo 正在更新台股題材新聞儀表板 v3...
python fetch_news.py --days 2 --max 12
echo.
echo 更新完成。請用 Chrome 或 Edge 打開 tw_stock_news_dashboard_v3.html
pause
