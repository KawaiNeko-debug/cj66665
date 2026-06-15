# h4 浏览器全自动抽奖

这个目录是浏览器流程版本：脚本只用 Playwright 操作页面按钮，不用 `requests/httpx` 主动发抽奖接口。中奖结果通过监听页面自己产生的响应和页面弹窗文本来记录。

## 运行

```powershell
pip install -r h4/requirements.txt
python -m playwright install chromium
python h4/script.py "账号" "密码" 1
```

如果当前目录已经是 `h4`，运行：

```powershell
python script.py "账号" "密码" 1
```

也可以用环境变量：

```powershell
$env:ACCOUNT_USERNAME="你的账号"
$env:ACCOUNT_PASSWORD="你的密码"
$env:ACCOUNT_INDEX="1"
python h4/script.py
```

默认会读取根目录 `.env`，也会额外读取 `h4/.env`。常用变量：

```env
PASSPORT_URL=https://passport.jlc.com/mobile/login?redirect=https%3A%2F%2Fm.jlc.com%2F
ACTIVITY_URL=https://m.jlc.com/pages-promo/brand-campaign/index?_embed=1&source=jlc_mobile_app&clientType=MP-WEIXIN
REFERER=https://servicewechat.com/wx6c7b851c877dba42/140/page-frame.html
SLIDER_ID=nc_1_n1z
WRAPPER_ID=nc_1__scale_text
H4_HEADLESS=false
H4_SIGNUP_TARGET=4
H4_EXCHANGE_TARGET=3
H4_DRAW_TARGET=3
GENERATE_XLSX=false
```

本地默认会把 JSON/xlsx 写到系统临时目录，汇总完成后自动清理；GitHub Actions 里会保留中间 JSON 用于合并，最终 xlsx 通过 TG Bot 发送后清理 artifact。

## 流程

1. 打开移动登录页，真实填写账号密码。
2. 如果出现滑块，就用浏览器事件拖动滑块。
3. 打开抽奖活动页面。
4. 在页面上寻找并点击报名按钮，最多 4 次，每次间隔 3-5 秒。
5. 在页面上寻找并点击兑换按钮，最多 3 次，每次间隔 3-5 秒；如果页面提示不能兑换就停止。
6. 在页面上寻找并点击抽奖按钮，最多 3 次，每次间隔 7-10 秒。
7. 监听页面自己的抽奖响应，记录奖品、中奖时间和默认 7 天过期时间。
