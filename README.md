# 积分抽奖（全员共享奖池）

纯前端单页抽奖，奖池状态通过远程 JSON 存储（默认 [Pantry](https://getpantry.cloud/) basket）在所有用户之间共享。打开 `index.html` 即可使用。

## 奖品规则

- 奖池共 **10** 个名额，**全员共用同一池子**。
- **固定保留** 3 份：`3000`、`2000`、`300` 积分各 1 份。
- 其余 **7** 份：在奖池首次生成时，随机生成 `1`–`2000`（含）的整数积分。
- 每次抽奖从剩余奖池中 **随机取出 1 份**（不放回）。
- 奖池抽空后提示「全部奖品已领完」。

## 参与规则

- **必须填写有效邮箱** 才能抽奖；页面会明确提示「未留邮箱无法参与」。
- **每个邮箱只能抽一次**（按规范化小写邮箱记录在远程 `claimedEmails`）。
- 中奖名单展示脱敏邮箱（如 `a***@x***.com`）与积分。

## 远程共享存储

页面通过 `window.LOTTERY_STORE` 读取配置：

```js
window.LOTTERY_STORE = {
  getUrl: '', // PARENT WILL FILL — Pantry basket GET URL
  putUrl: '', // PARENT WILL FILL — same basket URL for Pantry POST replace
  kind: 'pantry' // GET 读取；POST 整篮替换
};
```

若 `getUrl` 为空，页面显示 **「奖池未配置」**，无法抽奖。

### 状态结构

```json
{
  "version": 1,
  "pool": [3000, 2000, 300, /* …其余随机积分 */],
  "winners": [{ "email": "user@example.com", "points": 123, "at": "ISO-8601" }],
  "claimedEmails": ["user@example.com"]
}
```

- 首次成功连接时，若 `pool` 为空且 `winners` 为空，会自动初始化并 POST 保存。
- 抽奖流程：GET 最新状态 → 校验邮箱/空池 → 取出奖品 → `version++` → POST 整份状态；POST 失败则重新 GET 并最多重试 3 次（应对并发）。
- **奖池以远程存储为唯一真相来源**；`localStorage` 仅可选缓存最近一次 UI 展示，不参与裁决。

### Pantry 注意

- Pantry 免费 basket 在 **长期不活跃** 后可能过期或被清空，届时需重新创建 basket 并更新 `getUrl` / `putUrl`。
- 不要把 pantry ID 提交到公开仓库的固定配置里（或接受「知道 URL 的人都能读写奖池」）。

## 主办方重置

访问 `index.html?reset=1`，确认后会 POST 一份全新初始化的奖池（清空中奖记录）。仅供主办方使用。

## 部署前准备

1. 在 [getpantry.cloud](https://getpantry.cloud/) 创建 pantry 与 basket。
2. 把 basket URL 填进 `index.html` 里 `LOTTERY_STORE.getUrl` 与 `putUrl`（通常相同）。
3. 用静态托管（GitHub Pages、任意 CDN 等）发布本目录即可。

当前仓库中 URL 刻意留空（`// PARENT WILL FILL`），尚未部署。
