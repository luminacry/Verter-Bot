## 变更内容

<!-- 做了什么、为什么做。 -->

## 验证

- [ ] `just check` 通过
- [ ] 涉及 WebUI：`just e2e-docker` 通过；界面有预期变化时，已用 `just update-snapshots` 更新基线并逐张检查截图
- [ ] 涉及运行时热路径：`just bench` 的预算全部满足

## UI 自检（涉及 WebUI 时）

- [ ] 只使用通过 shadcn CLI 安装到 `src/components/ui` 的组件；如修改了其中文件，已在下方说明原因
- [ ] 颜色、圆角、阴影只使用主题 token
- [ ] 覆盖加载、空数据、错误三种状态
- [ ] 键盘可操作；亮色 / 暗色主题和中文 / 英文均已检查
- [ ] 新增文案同时提供 zh-CN 和 en
