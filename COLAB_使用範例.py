# Google Colab 使用範例
# 先把整個資料夾上傳到 Colab，再執行：

!python fetch_news.py --days 2 --max 12

from google.colab import files
files.download("tw_stock_news_dashboard_v3.html")
