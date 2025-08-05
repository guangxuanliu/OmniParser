# Gemini模型配置文件
# 这个文件用于配置Gemini模型的代理设置

# 代理设置（如果需要）
# 国内用户通常需要设置代理才能访问Gemini API
GEMINI_PROXY_URL = "http://192.168.139.80:10808"

# Gemini模型名称映射
GEMINI_MODELS = {
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-1.5-pro": "gemini-1.5-pro",
    "gemini-1.5-flash": "gemini-1.5-flash"
}

# 定价信息（每1M tokens的价格，USD）
GEMINI_PRICING = {
    "gemini-2.5-flash": 0.50,
    "gemini-1.5-pro": 3.50,
    "gemini-1.5-flash": 0.35
}

# 使用说明：
# 1. 如果你在国内，请将GEMINI_PROXY_URL设置为你的代理地址
# 2. 如果你不需要代理，请将GEMINI_PROXY_URL设置为None或空字符串
# 3. 确保你有有效的Gemini API密钥
# 4. 代理配置不会影响其他HTTP请求
