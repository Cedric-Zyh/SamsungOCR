# UI 架构重构路线图

## 当前边界

前端入口由 `static/app.js` 统一启动，功能代码按职责分为：

- `modules/shell`：应用组装、导航和页面生命周期
- `modules/core`：API、状态、轮询、DOM/UI 基础能力
- `modules/imports`：导入和识别方案
- `modules/workbench`：工作台、队列展示和任务动作
- `modules/records`：回单列表、筛选和批量操作
- `modules/review`：人工复核、证据和复核队列
- `modules/report`：质量统计

页面不再加载 `workbench.js`、`queue_state.js`、`import_files.js` 三个隐式全局脚本。对应能力由 ES Module 显式导入。

## 已完成的基础改造

1. 工作台、队列投影和文件导入能力迁入 `modules/core`。
2. 工作台控制器及其展示辅助迁入 `modules/workbench`。
3. API 层增加领域客户端，控制器可以通过 `services.records`、`services.queue`、`services.settings` 和 `services.report` 访问接口。
4. 状态增加按切片的 `patch` / `subscribe` 边界，结果失效通知不再直接散落修改多个状态袋。
5. 轮询定时器独立为 `core/polling.mjs`，负责生命周期、取消和防止重叠请求。
6. 页面使用单一 `ui.css` 和 `app.js` 入口，静态资源版本由文件修改时间生成。
7. 弹窗和全局菜单抽到 `templates/partials/dialogs.html`。
8. 工作台行渲染抽到 `modules/workbench/row_renderer.mjs`，筛选状态抽到 `filters.mjs`。
9. 复核中的日期、印章和签名确认编辑器抽到 `modules/review/editors.mjs`，复核协调器只负责会话和提交。

## 后续拆分顺序

1. 将 `workbench.mjs` 的数据加载、任务动作和选择状态继续拆成独立模块。
2. 将 `review.mjs` 的会话、导航和视图组装继续分开。
3. 将 `records.mjs` 的查询、表格渲染、选择和批量动作分开。
4. 把领域 API 客户端覆盖到剩余导入和复核请求，最后移除控制器中的 URL 字符串。
5. 把 `ui.css` 引用的历史样式文件迁移为基础、组件和功能样式，并用明确的层级替代时间性覆盖文件名。
6. 增加浏览器级冒烟测试，覆盖导入、工作台轮询、复核保存和设置持久化。

每一步都保留现有页面行为，并先通过模块测试和关键流程测试，再删除旧边界。
