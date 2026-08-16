/**
 * aiduPOP Visual Studio — Emoji Candidate Presets
 */

const EMOJI_CATEGORIES = [
  {
    name: "泡波与女性拟人 (Bubble Wave Icons)",
    emojis: [
      "🤹🏻‍♀️", "👩🏻‍🏫", "👩🏻‍🎨", "🕵🏻‍♀️", "👩🏻‍🚀", "👩🏻‍🔬", "👮🏻‍♀️",
      "👩🏻‍💻", "🥷🏻", "👷🏻‍♀️", "👩🏻‍⚖️", "👩🏻‍🎓", "👩🏻‍🔧", "🌊",
      "🫧", "✨", "🎶", "💦", "⚕", "💖", "🌸", "🧜🏻‍♀️"
    ]
  },
  {
    name: "经典工作流与工具 (Classic Workflow)",
    emojis: [
      "🏮", "📖", "🪄", "🔭", "📥", "🔍", "🗂️",
      "⚡", "🌐", "🐒", "🛡️", "📊", "💭", "🛠️",
      "⏱", "💡", "📦", "🚀", "🔥", "⚙️", "📌", "📁"
    ]
  },
  {
    name: "几何与极客符号 (Geometric & Tech)",
    emojis: [
      "◈", "◆", "▤", "✎", "⌕", "↓", "≡",
      "⊞", "▶", "◎", "❖", "✓", "∿", "※",
      "▲", "▼", "●", "■", "✦", "★", "⌘", "⌥"
    ]
  },
  {
    name: "状态与情感表情 (Reactions & Status)",
    emojis: [
      "🙆🏻‍♀️", "🧏🏻‍♀️", "💆🏻‍♀️", "🙋🏻‍♀️", "🙅🏻‍♀️", "💁🏻‍♀️",
      "✅", "⏳", "⚠️", "❌", "🎉", "🔥", "👀", "🤖"
    ]
  }
];

const TOOL_DEFINITIONS = [
  { key: "skill", label: "技能加载 (skill)", desc: "唤醒技能/载入锦囊" },
  { key: "read", label: "读取文件 (read)", desc: "查看代码/文档" },
  { key: "write", label: "写入编辑 (write)", desc: "修改/创建代码" },
  { key: "web_search", label: "全网搜索 (web_search)", desc: "搜索网络信息" },
  { key: "web_fetch", label: "网页抓取 (web_fetch)", desc: "下载/解析页面" },
  { key: "grep", label: "内容检索 (grep)", desc: "在代码库搜文本" },
  { key: "glob", label: "文件匹配 (glob)", desc: "按模式寻找文件" },
  { key: "exec", label: "命令执行 (exec)", desc: "终端/Bash 运行" },
  { key: "browser", label: "浏览器操作 (browser)", desc: "操控网页端" },
  { key: "agent", label: "子代理委派 (agent)", desc: "派发小猴子任务" },
  { key: "check", label: "状态核验 (check)", desc: "验证/把关结果" },
  { key: "analyze", label: "深度分析 (analyze)", desc: "推理与架构推演" },
  { key: "fallback", label: "未知工具 (fallback)", desc: "兜底未识别工具" }
];
