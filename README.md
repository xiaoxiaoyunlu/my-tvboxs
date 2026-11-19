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

**ghproxy.net 加速入口**

```text
https://ghproxy.net/https://raw.githubusercontent.com/xiaoxiaoyunlu/my-tvboxs/main/tv.json
```

**ghfast.top 加速入口**

```text
https://ghfast.top/https://raw.githubusercontent.com/xiaoxiaoyunlu/my-tvboxs/main/tv.json
```

<!-- tv-config-links:end -->

列表中的加速地址由 GitHub Actions 检查。在电视上使用前可以用同一 Wi-Fi 下的手机打开测试。若电视里已保存的加速域名停用，从这里复制一个可用地址替换。电视下次重新读取配置时会拿到更新。如果仍显示旧线路，重新加载配置并选列表第一条。

## 自动更新

GitHub Actions 每 6 小时从 `sources.json` 的目录发现线路并检查点播配置。在仓库的 [Actions](https://github.com/xiaoxiaoyunlu/my-tvboxs/actions) 中也可以打开 **Update TV config**，点 **Run workflow** 立即检查。

同一个 GitHub 文件只算一条线路。GitHub 线路优先使用能取回该文件的加速前缀，未通过检查时按顺序试下一个前缀或直连。README 的加速链接需要能读到仓库已发布的 `tv.json`，并且正文与本次任务检出的版本一致。工作流会把变更后的配置和链接一起提交。所有线路均未通过时，工作流报错并保留最近一次发布的配置。检测结果反映 Actions 的网络环境，具体影片仍以电视实测为准。

`sources.json` 的 `urls` 用来维护上游目录，`githubProxyPrefixes` 按优先级列出候选加速服务。在本机也可以用 Python 3.10 或更新版本执行 `python update_config.py` 来生成 `tv.json`。
