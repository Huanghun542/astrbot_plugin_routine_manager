# 📅 Routine Manager

<div align="center">

![License MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)
![AstrBot v4.0+](https://img.shields.io/badge/AstrBot-v4.0+-purple.svg)
![GitHub stars](https://img.shields.io/github/stars/Huanghun542/astrbot_plugin_routine_manager?style=social)

**一个 AstrBot 的智能作息管理插件**

*让 LLM 根据你的作息时间表提供更贴合场景的回复*

[项目仓库](https://github.com/Huanghun542/astrbot_plugin_routine_manager) · [安装指南](#-安装指南) · [使用说明](#-使用指南) · [API 文档](#-api-接口)

</div>

---

## 📖 插件简介

Routine Manager 是一个为 AstrBot 设计的作息管理插件。通过可视化的 WebUI 界面管理每周作息表,并将当前时间对应的行为动态注入到 LLM 的 system prompt 中,让机器人的回复更加贴合实际场景。

## ✨ 功能亮点

<table>
<tr>
<td align="center" width="25%">

### 📊 可视化排课
基于网格的拖拽式界面<br/>
Mon-Sun × 00:00-23:59

</td>
<td align="center" width="25%">

### 🔄 动态注入
实时判断当前行为<br/>
自动追加到人格 prompt

</td>
<td align="center" width="25%">

### 🔒 安全登录
一次性临时密钥<br/>
10 分钟有效期

</td>
<td align="center" width="25%">

### ⚡ 热加载
配置即时生效<br/>
无需重启机器人

</td>
</tr>
</table>

---

## 🚀 快速开始

### 系统要求

| 组件 | 版本要求 |
|------|----------|
| Python | 3.8 或更高版本 |
| AstrBot | v4.0 或更高版本 |
| 依赖 | quart, hypercorn |

### 📦 安装指南

#### 步骤 1: 下载插件
```bash
# 方法一: Git 克隆
git clone https://github.com/Huanghun542/astrbot_plugin_routine_manager.git

# 方法二: 直接下载 ZIP 文件并解压
```

#### 步骤 2: 部署插件
```bash
# 将插件文件夹移动到 AstrBot 插件目录
mv astrbot_plugin_routine_manager /path/to/AstrBot/data/plugins/
```

#### 步骤 3: 安装依赖
```bash
pip install -r data/plugins/astrbot_plugin_routine_manager/requirements.txt

# 或手动安装
pip install quart hypercorn
```

#### 步骤 4: 启动服务
- 启动 AstrBot 并在控制面板中启用插件
- 在配置中设置时区和端口(可选)

---

## 🎮 使用指南

### 开启管理后台

在聊天中发送以下命令(需要管理员权限):

```
作息管理 开启管理后台
```

机器人将返回:
- 一次性临时密钥(10 分钟有效,首次登录后失效)
- 访问地址: `http://[您的公网IP]:58101`

### 关闭管理后台

```
作息管理 关闭管理后台
```

### WebUI 操作流程

1. 使用浏览器访问返回的地址
2. 输入临时密钥登录
3. 在网格中添加/编辑时间块
4. 点击右下角"保存"按钮
5. 返回聊天测试,作息注入即可生效

### 使用示例

当你在 WebUI 中设置了 `08:30-12:00: 课程/学习` 后,在这个时间段内与机器人对话,LLM 将收到以下追加 prompt:

```
现在时间: 2025-11-10 10:30:00 当前行为: 课程/学习 请在语气和内容上贴合该场景进行回复。
```

---

## 🔧 配置说明

### 插件配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `timezone` | IANA 时区标识 | `Asia/Shanghai` |
| `inject_scope` | 注入范围 | `all` |
| `webui_port` | WebUI 端口 | `58101` |

### 注入范围选项

- `off`: 关闭注入功能
- `private`: 仅私聊时注入
- `group`: 仅群聊时注入
- `all`: 所有场景均注入

### 配置文件结构

配置保存在 `routine_config.json` 中:

```json
{
  "timezone": "Asia/Shanghai",
  "inject_scope": "all",
  "webui_port": 58101,
  "prompt": {
    "routine_prompt_template": "现在时间:{now} 当前行为:{action} 请在语气和内容上贴合该场景进行回复。"
  },
  "schedule": {
    "Mon": {
      "07:00-08:30": "起床/洗漱/早餐",
      "08:30-12:00": "课程/学习"
    },
    "Tue": {},
    "Wed": {},
    "Thu": {},
    "Fri": {},
    "Sat": {},
    "Sun": {}
  }
}
```

---

## 🔌 API 接口

### 身份验证

所有接口均需要已登录会话。未登录时返回 401 状态码。

### 获取配置

**请求**: `GET /api/load`

**响应**:
```json
{
  "ok": true,
  "data": {
    "timezone": "Asia/Shanghai",
    "inject_scope": "all",
    "schedule": { ... }
  }
}
```

### 保存作息表

**请求**: `POST /api/save`

**请求体**(支持两种格式):

格式一:
```json
{
  "schedule": {
    "Mon": {
      "07:00-08:00": "学习"
    }
  }
}
```

格式二:
```json
{
  "events": [
    {
      "weekday": 0,
      "start": "07:00",
      "end": "08:00",
      "title": "学习"
    }
  ]
}
```

### 更新完整配置

**请求**: `POST /api/config`

**请求体**:
```json
{
  "timezone": "Asia/Shanghai",
  "inject_scope": "all",
  "prompt": {
    "routine_prompt_template": "..."
  },
  "schedule": { ... }
}
```

**响应**:
```json
{
  "ok": true
}
```

---

## 🛡️ 部署与安全

### 网络配置

- WebUI 监听地址: `0.0.0.0:<webui_port>`
- Docker 端口映射: `-p 58101:58101`
- 需要在防火墙中开放相应 TCP 端口

### 安全建议

- 临时密钥默认 10 分钟过期,首次登录后立即失效
- 不要在公开聊天中转发登录链接
- 建议使用反向代理配置 HTTPS
- 定期检查访问日志

---

## 🛠️ 故障排查

### 常见问题

**Q1: 打开链接显示 500 错误或登录页不显示?**

检查 `assets/` 目录下是否存在前端页面文件(index.html 或 weekly.html)。

**Q2: 保存时提示 JSON 解析错误?**

确认已成功登录且会话有效。未登录时接口会返回 401 错误。

**Q3: 保存后配置未生效?**

插件会自动监测 `routine_config.json` 的修改时间并热加载。如果无效,尝试重启 AstrBot 或检查文件权限。

**Q4: 时间块不能跨天?**

当前版本不支持跨天时间块(如 23:30-07:00),这类配置会被忽略。

---

## 📂 项目结构

```
astrbot_plugin_routine_manager/
├── main.py                # 插件主逻辑
├── webui.py              # Web 服务器
├── assets/               # 前端静态文件
│   └── index.html
├── requirements.txt      # 项目依赖
└── routine_config.json   # 运行时配置
```

---

## 🗺️ 开发路线

- 时间块批量操作功能
- 导入/导出支持(JSON, CSV)
- 多用户协作与权限管理
- 可选的跨天时间段处理

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。

```
MIT License

Copyright (c) 2024 Huanghun542

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

<div align="center">

**如果这个插件对您有帮助,请考虑给个 ⭐ Star!**

Made with ❤️ by [Huanghun542](https://github.com/Huanghun542)

</div>