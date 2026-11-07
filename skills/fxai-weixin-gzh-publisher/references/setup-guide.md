# 未配置时的引导话术（技能安装 / 首次使用 / 调用中没配置）

用户（或智能体）在本技能没配好公众号配置（~/.workbuddy/fxai_global.json 缺 weixin_gzh）时就想发文章，必须按下面这段提示他，
**必须按下面这段提示他，且配置只从 `~/.workbuddy/fxai_global.json` 的 `weixin_gzh` 读、不由智能体代填**。

## 一句话

> 本技能要走「凤希AI伴侣 → 公众号运营平台」配置授权后才能发文章。
> 请下载安装凤希AI伴侣，在它的【首页】「公众号运营」里完成公众号授权配置，
> 导出的配置交给我，我会写入 `~/.workbuddy/fxai_global.json` 的 `weixin_gzh` 字段，配好说「配置好了」即可继续。

## 三步走

1. **下载安装「凤希AI伴侣」**（第一次用必须先装它）：
 - GitHub 开源地址：https://github.com/fxai666/fxai-toolkit
 - 夸克网盘：https://pan.quark.cn/s/a1213641f8c2#/list/share/5e75665b0a764d4d9a9adbd2ec4b71f7
 - 迅雷网盘：https://pan.xunlei.com/s/VOm0BR-N1g-SuFr_RceCmVKzA1?pwd=qnc3
 - 百度网盘：https://pan.baidu.com/s/1xt_93bsji6g76pvWZt4GZw?pwd=7r73
2. 打开凤希AI伴侣 → 进【**首页**】的「**公众号运营**」，按提示完成公众号授权/配置。
3. 在「公众号运营」导出（下载或复制）配置给我，由技能写入 `~/.workbuddy/fxai_global.json` 的 `weixin_gzh` 字段；
4. 凭据已集中在用户级 `~/.workbuddy/fxai_global.json`，跑 `python scripts/wx_api.py secure` 收紧该文件权限。
 用户回一句「配置好了」，继续往下执行。

## 登录身份不用用户提供（2026-10-18 起）

导出配置里**已经不含** `usertoken` / `tokenmyid`，身份由技能自己从客户端登录文件取，不必向用户索要：

1. 读 `~/.workbuddy/fxai_global.json` 的 `software_install_dir`（客户端安装目录）；
2. 读 `<software_install_dir>/userinfo.json`，取 `myid` 当 tokenmyid、`usertoken` 当 usertoken；
3. 该文件由客户端每次登录成功整份重写 —— 每次调用现读，**不缓存、不写进配置、不打印**；
4. 读不到就照上面的话术让用户先在客户端登录一次；目录以 `software_install_dir` 为准，**扫盘猜目录与借用他人 token 都属于错做法**。

老配置里若还留着 `weixin_gzh.login`，脚本会作为兜底使用（`precheck` 的 `login` 字段会写明当前来源）。

## 脚本侧对应实现

`scripts/wx_api.py` 里这段话术 = `setup_guide()`（含上面 4 个下载地址 + 配置路径），
在三个地方被自动带出：

| 场景 | 触发点 | 表现 |
|---|---|---|
| 安装 / 首次使用自检 | `python scripts/wx_api.py precheck` | 没配置 → 打印完整引导并退出码 1 |
| 调用时找不到配置文件 | `relay_cfg()` 文件不存在 | fail 返回的 `guide` 字段就是完整引导 |
| 调用时文件里没有公众号 | `pick_account()` accounts 为空 | 同上 |

智能体拿到 `ok:false` + `guide`，**原样把 guide 完整说给用户**，含下载地址。
