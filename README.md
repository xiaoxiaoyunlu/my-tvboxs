# my-tvboxs
自用，影视仓,搜集分享TVBox接口/影视仓接口

## 影视仓配置地址

首次更新成功后，在影视仓的「配置地址」填入下面的链接。有单仓模式选项时选单仓。

```text
https://raw.githubusercontent.com/xiaoxiaoyunlu/my-tvboxs/main/tv.json
```

家里的电视直连 GitHub Raw 不通时，可以用同一 Wi-Fi 下的手机试一下加速地址。

```text
https://gh-proxy.com/https://raw.githubusercontent.com/xiaoxiaoyunlu/my-tvboxs/main/tv.json
```

电视下次重新读取配置时会拿到更新。如果仍显示旧线路，重新加载配置并选列表第一条。

## 自动更新

GitHub Actions 每 6 小时从 `sources.json` 的目录发现线路并检查点播配置。在仓库的 [Actions](https://github.com/xiaoxiaoyunlu/my-tvboxs/actions) 中也可以打开 **Update TV config**，点 **Run workflow** 立即检查。

通过检查的线路会按优先级写入 `tv.json`。首选失效时，下一条可用线路排到第一位。所有线路均未通过时，工作流会报错并保留最近一次发布的配置。在 Logs 中可查看跳过原因。检测结果反映 Actions 的网络环境，具体影片仍以电视实测为准。

需要新增线路时，编辑 `sources.json`。在本机也可以用 Python 3.10 或更新版本执行 `python update_config.py`。
