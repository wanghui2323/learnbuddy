# LearnBuddy · 开源学习地图

这里不仅解释怎样启动项目，也帮助你追踪一个学习需求怎样变成页面、代码与验收。当前入口以 **[学习地图.html](./学习地图.html)** 为主；版本与任务去 **[开发工作台](./LearnBuddy项目工作台.html#open-learning)**。

配套导读：[100 天 AI Builder 第一周：把 LearnBuddy 的构建过程一起开源](./AIBuilder第一周.md)。

## 按学习顺序打开产物

| 学习问题 | 当前依据与源码 | 可视化入口 |
|---|---|---|
| 为什么先摸底，再生成路径？ | [SPEC / ADR-055](../SPEC.md)、[基线服务](../core/baseline.py) | [产品闭环](./学习地图.html#product) |
| 用户怎样拥有自己的学习伙伴？ | [Cloud / Personal](./07-开源与公有云双版本架构.md) | [产品判断](./学习地图.html#product) |
| 产品语义怎样影响设计？ | [设计规范](./设计规范.html#implementation)、[当前页面 CSS](../web/index.html)、[公共状态](../web/common.css) | [Design Tokens 与页面](./学习地图.html#design) |
| 页面如何保存学习事实？ | [API](../server.py)、[数据模型](../core/db.py)、[后台任务](../core/jobs.py) | [架构责任图](./学习地图.html#architecture) |
| Agent 怎样改变同一份学习状态？ | [工具契约](../core/tools.py)、[MCP](../mcp_server.py)、[插件](../integrations/openclaw-learnbuddy/index.js) | [对话到写入](./学习地图.html#agent) |
| 需求如何进入一轮开发？ | [ADR](../SPEC.md)、[发布门](../scripts/release_loop.py) | [开发工作台](./LearnBuddy项目工作台.html#open-learning)、[V0.53 任务树](./LearnBuddy项目工作台.html#iteration-v053) |
| 如何复查质量与失败？ | [工程测试](../tests/)、[Eval 样本](../tests/fixtures/assessment_eval_cases.json) | [学习练习与验收边界](./学习地图.html#verify) |

## 如何看可视化页面

GitHub 的 HTML 链接默认展示源码，不等于页面已托管。克隆仓库后，在仓库根目录运行：

```bash
python3 -m http.server 8088 --bind 127.0.0.1
```

打开 `http://127.0.0.1:8088/docs/学习地图.html`，可以浏览图示、截图、设计规范与开发工作台。仅在可信本机使用；不要将这个静态服务器暴露到公网。真正运行学习产品，请按 [根 README](../README.md) 启动 FastAPI，HTML 页面不替代后端。

## 当前与历史

- 当前：线上记录 V0.52，公开源码 v0.53.0-rc.1；最近一次开发回归累计 454 tests。
- 待验收：干净机 Docker / 恢复、真实 OpenClaw / 飞书、36 个开放题双人人工评分单元、生产部署。
- [设计规范](./设计规范.html)是目标合同；实际页面仍有重复 CSS 变量，不是全站统一已完成。
- [架构总览](./架构总览.html)保留 V0.18 历史快照；[早期需求方案](./01-需求方案.md)保留当时分期。历史方案不覆盖最新 ADR。
- [文档中心](./文档中心.html)是分类目录；[开发工作台](./LearnBuddy项目工作台.html)是人工维护的版本视图；`SPEC.md` 保存决策原文，代码与测试验证实现。
- 早期的 [工作台](./工作台.html)、[开发方案与计划](./03-开发方案与计划.md)只作专题和演进参考。

## 维护与复用

先选择一个用户问题，连接需求、页面、接口、对象、测试和结果。截图注明真实页面/静态渲染/历史原型，状态注明版本与未验证项。不要把私人数据、凭据、原始私人对话加入过程开源。

本页验证：`python3 scripts/verify_learning_map.py`。截图再生成：`node scripts/capture_learning_map.mjs`（需要本机 Codex Playwright wrapper，或通过 `LEARNBUDDY_PWCLI` 指定兼容的 playwright-cli 可执行路径）。
