# AI 日报 —— 每日 AI 信息过滤器

每天自动从 GitHub、Hacker News、arXiv 等多个信息源抓取最有价值的 AI 资讯，用 DeepSeek 生成全中文日报，部署为精美的手机友好网页，并通过企业微信/邮件推送通知。

---

## 你每天会收到什么？

📱 **企业微信消息**（早上 8:00）：3 条今日必知 + 完整日报链接

🌐 **点击链接**：打开精美卡片网页，包含 6 大板块：

| 板块 | 内容 | 数量 |
|---|---|---|
| 今日必知 | 当天最重要的 AI 大事 | 1-3 条 |
| 大佬说了啥 | AI 领域关键人物的最新观点 | 2-3 条 |
| 值得读的论文 | arXiv 新论文的大白话解读 | 1-2 篇 |
| 热门开源项目 | GitHub 热门 Agent/Skill/工具 | 3-5 个 |
| 落地风向标 | 谁在用 AI 做产品/赚钱 | 1-2 条 |
| 趋势洞察 | 今天的 AI 世界在往哪走 | 3 条 |

📧 **邮件备份**（同时发送）：精简摘要 + 网页链接

---

## 部署教程（保姆级）

预计 20 分钟完成。

---

### 第一步：创建 GitHub 仓库并上传文件

1. 登录 GitHub（没账号先注册：https://github.com）
2. 点右上角 **+** → **New repository**
3. 填写：
   - 仓库名：`agent-daily-report`
   - 可见性：**Private**（私有）
4. 点 **Create repository**
5. 在空仓库页面点 **uploading an existing file**
6. 把解压后的所有文件拖进去（包括 `.github` 隐藏文件夹）
   - Mac 显示隐藏文件：`Cmd + Shift + .`
   - Windows：文件管理器 → 查看 → 勾选「隐藏的项目」
7. 点 **Commit changes**

---

### 第二步：开启 GitHub Pages

1. 进入仓库 → **Settings**（顶部标签栏）
2. 左侧菜单找到 **Pages**
3. **Source** 选择 **Deploy from a branch**
4. **Branch** 选择 `main`，文件夹选择 `/docs`
5. 点 **Save**
6. 等 1-2 分钟，页面顶部会显示你的网址，形如：
   `https://你的用户名.github.io/agent-daily-report`
7. **复制这个网址**，后面要用

---

### 第三步：获取各项密钥

#### 3.1 DeepSeek API Key

1. 打开 https://platform.deepseek.com/
2. 注册登录 → 左侧 **API Keys** → **创建 API Key**
3. 复制保存（形如 `sk-xxxxxxxx`）

#### 3.2 企业微信群机器人

1. 打开企业微信，创建一个群（或用已有的群，哪怕只有你自己也行）
2. 点击群右上角 **...** → **群机器人** → **添加群机器人**
3. 机器人名称填 `AI 日报`
4. 创建后会显示一个 **Webhook 地址**，形如：
   `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx`
5. **复制这个地址**

#### 3.3 邮箱 SMTP（备份渠道）

**Gmail：**
1. https://myaccount.google.com/security → 开启两步验证
2. 两步验证页面底部 → 应用专用密码 → 创建 → 复制 16 位密码

**QQ 邮箱：**
1. mail.qq.com → 设置 → 账户 → 开启 SMTP 服务 → 复制授权码

**163 邮箱：**
1. mail.163.com → 设置 → POP3/SMTP → 开启 → 设置授权密码

#### 3.4 GitHub Token（可选但推荐）

1. https://github.com/settings/tokens
2. **Generate new token (classic)** → 勾选 `public_repo` → 生成 → 复制

---

### 第四步：配置 GitHub Secrets

进入仓库 → **Settings** → 左侧 **Secrets and variables** → **Actions** → **New repository secret**

逐个添加：

| 第几个 | Name（照抄） | Secret（填你自己的） |
|---|---|---|
| 1 | `WECOM_WEBHOOK_URL` | 企业微信机器人 Webhook 地址 |
| 2 | `DEEPSEEK_API_KEY` | DeepSeek API Key |
| 3 | `GITHUB_PAGES_URL` | 第二步获得的网址（如 `https://xxx.github.io/agent-daily-report`） |
| 4 | `SMTP_SERVER` | SMTP 服务器（Gmail: `smtp.gmail.com` / QQ: `smtp.qq.com`） |
| 5 | `SMTP_PORT` | `587` |
| 6 | `SMTP_USER` | 你的发件邮箱 |
| 7 | `SMTP_PASSWORD` | 邮箱应用专用密码/授权码 |
| 8 | `TO_EMAIL` | 收件邮箱 |
| 9 | `GH_PAT` | GitHub Token（如果做了 3.4） |

> 每个 Secret 的添加方法：点 **New repository secret** → Name 填名称 → Secret 填值 → 点 **Add secret**

---

### 第五步：测试运行

1. 进入仓库 → **Actions** 标签
2. 如有黄色提示，点 **I understand my workflows, go ahead and enable them**
3. 左侧点 **每日 AI 日报** → 右侧 **Run workflow** → **Run workflow**
4. 等 2-3 分钟，状态变绿 ✅ 后：
   - 企业微信群里应该收到了摘要消息
   - 邮箱里应该收到了邮件
   - 打开你的 GitHub Pages 网址，能看到完整的卡片日报

### 第六步：大功告成！🎉

从此每天早上 8:00，你在企业微信里就能收到 AI 日报推送。通勤路上点开链接，5 分钟掌握 AI 圈最新动态。

---

## 后续调整

### 修改推送时间

编辑 `.github/workflows/daily_report.yml`：

| 北京时间 | cron 值 |
|---|---|
| 7:00 | `'0 23 * * *'` |
| 8:00 | `'0 0 * * *'`（默认） |
| 9:00 | `'0 1 * * *'` |

### 修改关注方向

编辑 `daily_report.py` 顶部的 `KEYWORDS` 列表，添加或删除关键词。

### 暂停/恢复日报

Actions → 左侧选工作流 → 右上角 **...** → **Disable/Enable workflow**

---

## 费用

- **GitHub Actions**：每月 2000 分钟免费，每次运行约 2 分钟，完全够用
- **GitHub Pages**：免费
- **DeepSeek**：每次不到 0.01 元人民币
- **企业微信机器人**：免费
- **总计**：几乎零成本

---

## 常见问题

**Q：企业微信没收到消息？**
检查 `WECOM_WEBHOOK_URL` 是否正确复制。在浏览器里直接访问这个 URL（去掉 send 后面的参数），看是否返回正常。

**Q：网页打不开？**
确认已在 Settings → Pages 中配置好 Source 为 main 分支的 /docs 文件夹。首次部署需要等 2-5 分钟。

**Q：日报内容是英文的？**
DeepSeek API Key 未生效。检查 `DEEPSEEK_API_KEY` Secret。

**Q：GitHub Actions 60 天后停了？**
仓库无活动 60 天后 Actions 自动暂停。去 Actions 页面重新启用即可。

---

## 项目结构

```
agent-daily-report/
├── .github/workflows/
│   └── daily_report.yml    ← 定时任务 + Pages 部署
├── docs/                   ← 网页日报存放目录（自动生成）
│   ├── index.html          ← 最新一期
│   └── 2026-03-28.html     ← 按日期归档
├── daily_report.py         ← 主脚本
├── .gitignore
└── README.md
```

## 许可

MIT License
