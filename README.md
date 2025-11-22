<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_routine_manager?name=astrbot_plugin_routine_manager&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_routine_manager

_📅 [astrbot](https://github.com/AstrBotDevs/AstrBot) 智能作息管理插件 ✨_  

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-Huanghun542-blue)](https://github.com/Huanghun542)

</div>

## 💡 介绍

一个为 AstrBot 打造的智能化作息管理插件！  
它提供了一个**现代化的可视化 WebUI**，允许你通过拖拽轻松管理周日程。最核心的功能是利用 AstrBot 的 Hook 机制，**根据当前的作息状态动态调整 LLM 的人设（System Prompt）**，让 Bot 知道它现在是在“上课”、“睡觉”还是“工作”，从而表现出更贴合场景的语气和回复。

### ✨ 核心特性
- **可视化 WebUI**: 现代化的周视图日程表，支持拖拽查看、双击添加、颜色标记。
- **无损注入**: 在 LLM 请求发出前一刻动态修改 System Prompt，不污染原始人格配置。
- **热更新**: WebUI 保存后立即生效，无需重启机器人。
- **安全机制**: 管理后台使用一次性密钥（OTP）登录，10 分钟自动过期。

## 📦 安装

### 1. 导入插件
- **方法一（推荐）**：直接在 AstrBot 的插件市场搜索 `astrbot_plugin_routine_manager` 点击安装。
- **方法二（手动）**：克隆本仓库到插件文件夹：

```bash
cd /AstrBot/data/plugins
git clone https://github.com/Huanghun542/astrbot_plugin_routine_manager
2. 安装依赖
本插件需要额外的 Web 框架支持，请在 AstrBot 环境下运行：
code
Bash
pip install quart hypercorn
3. 重启
控制台重启 AstrBot 以加载插件。
⚙️ 配置
主要配置通过 WebUI 进行，高级配置可在 routine_config.json 中修改：
timezone: 时区设置（默认 Asia/Shanghai）
inject_scope: 注入生效范围 (all / private / group / off)
webui_port: 后台端口（默认 58101）
⌨️ 使用说明
1. 开启管理后台
在聊天窗口（仅限管理员）发送指令，获取临时的访问地址和密钥。
2. 配置作息
浏览器打开机器人回复的 URL。
输入密钥登录。
双击 空白网格添加日程（例如：08:00-10:00 上课）。
点击右下角 “保存配置” 按钮。
3. 验证效果
配置完成后，当时间处于设定的日程范围内时，LLM 的 System Prompt 会自动追加类似以下内容：
现在时间：2025-11-22 09:30:00 当前行为：上课 请在语气和内容上贴合该场景进行回复。
⌨️ 指令表
指令	说明
作息管理 开启管理后台	生成 WebUI 访问链接及临时登录密钥
示例图
(在此处放一张你的 WebUI 截图，例如 assets/preview.png)
![alt text](https://via.placeholder.com/800x400?text=WebUI+Preview+Image)
🤝 TODO

可视化周视图日程表

LLM 动态 Prompt 注入

安全密钥登录机制

支持更多自定义注入模板

移动端 UI 适配优化
👥 贡献指南
🌟 Star 这个项目！（点右上角的星星，感谢支持！）
🐛 提交 Issue 报告问题
💡 提出新功能建议
🔧 提交 Pull Request 改进代码
