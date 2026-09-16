# LOCAL_MAINTENANCE — MediaCrawler 维护说明

> 2026-09-16 建：用户已在 GitHub fork 本项目，远端按双远端约定配置完毕。

## 远端与分支

| 名称 | 指向 | 角色 |
|---|---|---|
| `origin` | ningnuo-dot/MediaCrawler | **用户 fork**（推送目标） |
| `upstream` | NanmiCoder/MediaCrawler | 官方上游（更新来源） |

| 分支 | 内容 |
|---|---|
| `main` | 跟随上游 + 极少量本机配置（当前仅 `CDP_CONNECT_EXISTING=False` 一处） |
| `local-custom` | 2026-08~09 实测修复与工具存档（douyin 评论洞察、知乎问题排序等 37 文件，**仅代码**） |

## 配置与密钥边界

- `config/base_config.py` 是本机运行配置（CDP 开关、保存选项等），改动**允许提交**（当前无凭证）。
- **登录 cookie、token 等凭证一律不提交 Git**——登录态存于浏览器 profile 与 `browser_data/`，天然不入库；若未来把 cookie 写进 config，须改用环境变量或本地未跟踪文件。

## 上游更新流程（每次更新照此执行）

```bash
cd /d/GitHub/MediaCrawler
git fetch upstream                                  # 1. 取官方更新
git log --oneline main..upstream/main               # 2. 看更新范围与发行说明
git checkout -b update-candidate upstream/main      # 3. 开候选分支，把上游合进来
git merge local-custom                              #    （如需带本地定制）逐个解决冲突，冲突停留在候选分支
# 4. 候选分支上：重装依赖变化部分、跑通一次采集、验证数据落库格式
# 5. 验证通过后才合入 main 并推送 fork：
git checkout main && git merge update-candidate
git push origin main
git branch -d update-candidate
```

- 不直接在 `main` 上 `git pull upstream`；不动正在运行的采集环境。
- 上游大版本重构（数据库格式、接口）先单独备份 `media_data/` 与数据库再动。

## 回退

- 回退代码：`git checkout <旧提交或分支>`；采集出的数据在 `media_data/` 与数据库，与代码版本无关，回退代码不影响已采数据。
