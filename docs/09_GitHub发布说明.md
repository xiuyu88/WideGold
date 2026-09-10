# GitHub 发布说明

## 1. 解压

将本发布包解压，进入：

```text
widegold-agent/
```

确认不要把本机 `.env`、数据库备份或真实 API Key 放进仓库。

## 2. 初始化 Git

```bash
git init
git add .
git commit -m "feat: 发布 WideGold Agent V1"
git branch -M main
```

在 GitHub 新建空仓库后：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

## 3. 推荐仓库设置

- 默认分支：`main`；
- 开启 GitHub Actions；
- 开启 Secret scanning / Dependabot（如账号支持）；
- 不要在 Actions Secret 中提交不需要的真实生产密码；
- Live LLM Test 不放到普通 PR CI。

## 4. License

本包没有替你决定开源许可证。公开仓库前请根据你的发布意图选择，例如 MIT、Apache-2.0 或保留全部权利。

如果要真正开源，建议明确添加 `LICENSE`，避免使用者对授权范围产生歧义。

## 5. Release

推荐 Git Tag：

```bash
git tag -a v1.0.0 -m "WideGold Agent V1"
git push origin v1.0.0
```

Release 描述可直接引用 `CHANGELOG.md` 与 `docs/08_本次发布验证报告.md`。
