{
  "id": "knowledge-evolution",
  "name": "知识库自我进化",
  "nameI18n": {
    "zh-CN": "知识库自我进化",
    "en-US": "Knowledge Evolution",
    "ja-JP": "ナレッジベース自己進化"
  },
  "icon": "🧬",
  "type": "aapp",
  "version": "1.1.0",
  "developer": "violin",
  "description": "Three-step structured knowledge base evolution: scan → connection discovery → action list. v1.0.0: 4-dimension quality scoring, 3-layer connection discovery, 3 action execution paths, create-only with references. v1.1.0: 🕰️ Historical Perspective — new content as trigger to find hidden connections in your OLD accumulation.",
  "descriptionI18n": {
    "zh-CN": "三步结构化知识库进化：扫描 → 关联发现 → 行动清单。v1.1.0 新增 🕰️ 历史视角——让新内容主动去照亮你的旧积累。",
    "en-US": "Three-step structured knowledge base evolution: scan → connection discovery → action list. v1.1.0 adds 🕰️ Historical Perspective — let new content trigger hidden connections in your old accumulation."
  },
  "runtime": {
    "type": "embedded"
  },
  "activationAction": {
    "method": "GET",
    "path": "/"
  },
  "chatMenu": [
    {
      "label": "🚀 一键进化",
      "labelI18n": {"zh-CN": "🚀 一键进化", "en-US": "🚀 Quick Evolve"},
      "children": [
        {
          "label": "周度进化",
          "labelI18n": {"zh-CN": "周度进化", "en-US": "Weekly"},
          "action": {
            "method": "GET",
            "path": "/scan_ui",
            "params": {"range_type": "week"},
            "prompt": "运行周度进化扫描"
          }
        },
        {
          "label": "月度体检",
          "labelI18n": {"zh-CN": "月度体检", "en-US": "Monthly"},
          "action": {
            "method": "GET",
            "path": "/scan_ui",
            "params": {"range_type": "month"},
            "prompt": "运行月度体检"
          }
        }
      ]
    },
    {
      "label": "🔍 深挖",
      "labelI18n": {"zh-CN": "🔍 深挖", "en-US": "🔍 Deep Dive"},
      "action": {
        "method": "GET",
        "path": "/deep_dive_ui",
        "prompt": "打开专题深挖表单"
      }
    },
    {
      "label": "📚 历史",
      "labelI18n": {"zh-CN": "📚 历史", "en-US": "📚 History"},
      "action": {
        "method": "GET",
        "path": "/history_ui",
        "prompt": "查看历史进化报告"
      }
    }
  ],
  "shortcuts": {
    "type": "static",
    "items": [
      {
        "name": "evo",
        "method": "POST",
        "path": "/deep_dive_ui",
        "description": "Quick deep dive: <<evo <topic>>",
        "descriptionI18n": {
          "zh-CN": "快捷专题深挖：<<evo <topic>>",
          "en-US": "Quick deep dive: <<evo <topic>>"
        }
      }
    ]
  }
}
