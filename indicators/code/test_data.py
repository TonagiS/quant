import akshare as ak

print("正在通过腾讯接口拉取沪深300ETF数据，请稍等...")

# 使用腾讯接口获取数据
# 注意：腾讯接口需要带交易所前缀，'sh' 代表上海，'sz' 代表深圳
df = ak.stock_zh_a_hist_tx(
    symbol="sh510300",
    start_date="20230101",
    end_date="20261231",
    adjust="qfq"  # 获取前复权数据，避免分红送股造成的价格断层
)

# 打印前 5 行看看长什么样
print(df.head())
print("总共拉取到的行数:", len(df))
print("包含的列名:", list(df.columns))