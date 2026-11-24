# my-tvboxs
自用，影视仓,搜集分享TVBox接口/影视仓接口

## 影视仓配置地址

复制下面任一地址，填入影视仓的「配置地址」。有单仓模式选项时选单仓。

<!-- tv-config-links:start -->

**GitHub Raw 固定入口**

```text
https://raw.githubusercontent.com/xiaoxiaoyunlu/my-tvboxs/main/tv.json
```

**gh-proxy.com 加速入口**

```text
https://gh-proxy.com/https://raw.githubusercontent.com/xiaoxiaoyunlu/my-tvboxs/main/tv.json
```

**ghfast.top 加速入口**

```text
https://ghfast.top/https://raw.githubusercontent.com/xiaoxiaoyunlu/my-tvboxs/main/tv.json
```

<!-- tv-config-links:end -->

列表中的加速地址由 GitHub Actions 检查。在电视上使用前可以用同一 Wi-Fi 下的手机打开测试。若电视里已保存的加速域名停用，从这里复制一个可用地址替换。电视下次重新读取配置时会拿到更新。如果仍显示旧线路，重新加载配置并选列表第一条。

## 自动更新

GitHub Actions 每小时第 17 分检查 `tv.json` 里已发布的线路，实际启动可能延迟。检查包括实际配置、站点列表和声明的 Spider/JAR。首次失败的线路会复查一次。可用率按通过检查的线路数除以已发布线路总数计算，达到 60% 时只留下检查记录。

可用率低于 60% 时，任务会从 `sources.json` 的上游目录查找替代地址。通过检查的线路保留原地址、名称和顺序，尚无替代的线路等下次继续检查。找到可替换的地址后，任务再刷新 README 的加速入口并推送变化。首次缺少配置时会直接生成。推送变更和在 [Actions](https://github.com/xiaoxiaoyunlu/my-tvboxs/actions) 点 **Run workflow** 都可触发一次相同的检查。

同一个 GitHub 文件只算一条线路。GitHub 线路优先使用能取回该文件的加速前缀，未通过检查时按顺序试下一个前缀或直连。README 的加速入口按返回的线路列表验证。所有线路均未通过且没有找到替代地址时，工作流报错并保留最近一次发布的配置。检测结果反映 Actions 的网络环境，具体影片仍以电视实测为准。

`sources.json` 的 `urls` 用来维护上游目录，`githubProxyPrefixes` 按优先级列出候选加速服务。在本机也可以用 Python 3.10 或更新版本执行 `python update_config.py` 来生成 `tv.json`。
