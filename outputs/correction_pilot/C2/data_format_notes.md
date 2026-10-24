# CSDS 格式检查（处理代码编写前完成）

数据是 JSON 数组，每项有 DialogueID、QRole、Session_id、Dialogue、UserSumm、AgentSumm、FinalSumm、QA。Dialogue 的每条消息含 speaker（Q=咨询方，A=客服）、turn（从0开始的消息序号）、utterance。UserSumm 是字符串列表；QA 有问题/回答摘要、对应消息索引、intent。

用户连发多条分别记录，连续 Q 合并成用户轮次，用换行保留。原文存在分词空格；保存对照表原文，关键词、相似度计算前去空白，prompt 历史保留原文。并非所有 Q 都是顾客：抽样限定 QRole=用户；不从商家、物流师傅等角色抽样。这是角色筛选，不是解决状态筛选。

有 UserSumm，其中有“用户同意赔偿京豆。”等结果陈述；也有“用户询问退款是否成功。”这样的诉求，不能仅凭“成功”二字删除。没有独立的已解决/未解决标签，不能把客服摘要当成解决标签。

首版摘要删除规则（现行规则见文末 Delta）：按句号、分号及逗号切分，去掉含“最终/最后/问题解决了/问题已解决/退款成功/退货成功/用户（可带“表示”）同意/接受/满意/用户表示感谢/用户决定自行解决”的结果分句；含询问、是否、能否、希望、要求、想、未、没有、不成功、之前、此前的分句保留（问题状态或诉求）。规则只去明确结果，不保证识别全部语义；删除的分句另存 summary_audit.json。为空时使用“咨询购物售后问题”，不从后续对话补目标。不使用 AgentSumm、FinalSumm、QA 回答。完整用户诉求摘要可能含稍后才提出的诉求，这是合同给定的目标输入，区别于严格截断的历史。

分集数量：{'test': 800, 'train': 9101, 'val': 800}；总计 10701；DialogueID 全局唯一。平均原始消息数 25.9227；平均合并用户轮次数 8.2076。QRole 分布：{'用户': 9910, '商家': 727, '物流师傅': 61, '供应商': 1, '师傅': 2}。6–30轮按合并用户轮次解释。订单等信息有 [订单编号]、[数字] 脱敏占位，8位数字指标不能还原真实订单披露率。数据支持合同定义，未发现无法适配的结构差异。

## 完整原文样例：test.json 第一项

```json
{
  "DialogueID": 5900,
  "QRole": "用户",
  "QA": [
    {
      "QueSumm": "用户询问改密码除手机验证外的其他方法。",
      "AnsSummShort": "客服让用户提供号码，交给专员回电解决。",
      "AnsSummLong": "客服让用户提供号码，交给专员回电解决。",
      "QueSummUttIDs": [
        2,
        3
      ],
      "AnsSummShortUttIDs": [
        11
      ],
      "AnsSummLongUttIDs": [
        11
      ],
      "QASumm": "用户询问改密码除手机验证外的其他方法。客服让用户提供号码，交给专员回电解决。",
      "intent": "找回密码"
    },
    {
      "QueSumm": "用户询问密码登录后，怎样可以不用手机号验证。",
      "AnsSummShort": "客服帮助反馈给专员。",
      "AnsSummLong": "客服帮助反馈用户的问题给专员。",
      "QueSummUttIDs": [
        21
      ],
      "AnsSummShortUttIDs": [
        23
      ],
      "AnsSummLongUttIDs": [
        21,
        23
      ],
      "QASumm": "用户询问密码登录后，怎样可以不用手机号验证。客服帮助反馈给专员。",
      "intent": "手机邮件相关问题"
    },
    {
      "QueSumm": "用户询问不验证是否可以买东西。",
      "AnsSummShort": "客服请用户耐心等待。",
      "AnsSummLong": "客服请用户耐心等待。",
      "QueSummUttIDs": [
        25
      ],
      "AnsSummShortUttIDs": [
        26
      ],
      "AnsSummLongUttIDs": [
        26
      ],
      "QASumm": "用户询问不验证是否可以买东西。客服请用户耐心等待。",
      "intent": "手机邮件相关问题"
    }
  ],
  "Session_id": "bb858d8802888c7fd04f509dbf39878d",
  "Dialogue": [
    {
      "speaker": "A",
      "turn": 0,
      "utterance": "有 什么 问题 我 可以 帮 您 处理 或 解决 呢 ?"
    },
    {
      "speaker": "Q",
      "turn": 1,
      "utterance": "你好"
    },
    {
      "speaker": "Q",
      "turn": 2,
      "utterance": "以前 的 手机号码 销号 了 ， 密码 也 忘 了"
    },
    {
      "speaker": "Q",
      "turn": 3,
      "utterance": "改 密码 需要 手机 验证码"
    },
    {
      "speaker": "A",
      "turn": 4,
      "utterance": "还 记得 您 以前 的 手机号码 吗"
    },
    {
      "speaker": "Q",
      "turn": 5,
      "utterance": "[数字]"
    },
    {
      "speaker": "A",
      "turn": 6,
      "utterance": "稍 等"
    },
    {
      "speaker": "Q",
      "turn": 7,
      "utterance": "注册 邮箱 [邮箱]"
    },
    {
      "speaker": "A",
      "turn": 8,
      "utterance": "若晨 [数字]"
    },
    {
      "speaker": "A",
      "turn": 9,
      "utterance": "是 这个 用户名 吗"
    },
    {
      "speaker": "Q",
      "turn": 10,
      "utterance": "嗯 嗯"
    },
    {
      "speaker": "A",
      "turn": 11,
      "utterance": "这边 帮 您 升级 专员       会 在 明天 [数字] 点前 给 您 回电         方便 提供 下 您 要 绑定 的 手机号码 吗"
    },
    {
      "speaker": "Q",
      "turn": 12,
      "utterance": "[数字]"
    },
    {
      "speaker": "Q",
      "turn": 13,
      "utterance": "其实 个人 觉得   通过 注册 邮箱 验证 挺 方便 的"
    },
    {
      "speaker": "A",
      "turn": 14,
      "utterance": "已经 为 您 反馈 专员 ， 请 您 保持 电话 畅通 哦 ， 会 尽快 联系 您 的 呢"
    },
    {
      "speaker": "A",
      "turn": 15,
      "utterance": "请问 还有 其他 还 可以 帮到 您 的 吗 ?"
    },
    {
      "speaker": "Q",
      "turn": 16,
      "utterance": "好 的 ， 没 了 ， 谢谢"
    },
    {
      "speaker": "A",
      "turn": 17,
      "utterance": "妹子 怠慢 的 地方 ， 您 多多 包含 哦 ， 辛苦 您 点击 聊天 框中 的 [站点] 给 我 一个 评价 哦 ， 妹子 期待 再次 为您服务  "
    },
    {
      "speaker": "A",
      "turn": 18,
      "utterance": "亲       请问 还有 什么 我能 帮助 到 您 的 呢"
    },
    {
      "speaker": "Q",
      "turn": 19,
      "utterance": "你好 ， 您 可以 看见 我 之前 反馈 的 问题 吗"
    },
    {
      "speaker": "A",
      "turn": 20,
      "utterance": "可以 的       亲"
    },
    {
      "speaker": "Q",
      "turn": 21,
      "utterance": "嗯 ， 我 现在 终于 想起 密码 了 ， 登录 进来 了 ， 但 还是 要 验证 我 以前 的 手机号码"
    },
    {
      "speaker": "A",
      "turn": 22,
      "utterance": "没事 的 哦     亲"
    },
    {
      "speaker": "A",
      "turn": 23,
      "utterance": "我们 这边 已经 帮 您 反馈 专员 了"
    },
    {
      "speaker": "Q",
      "turn": 24,
      "utterance": "不能 邮箱 改 密码 就算 了 ， 找到 密码 还 必须 验证 以前 的 手机号码"
    },
    {
      "speaker": "Q",
      "turn": 25,
      "utterance": "不 验证 现在 可以 买 东西 吗"
    },
    {
      "speaker": "A",
      "turn": 26,
      "utterance": "还 请 您 耐心 等待 下"
    },
    {
      "speaker": "Q",
      "turn": 27,
      "utterance": "好 吧 ， 这么 麻烦 ， 不合理 ， 再见"
    },
    {
      "speaker": "A",
      "turn": 28,
      "utterance": "亲     这个 是 要 按照 流程 走 的 哦       也 是 为 您 账户 安全 着想"
    },
    {
      "speaker": "A",
      "turn": 29,
      "utterance": "小妹 也 理解 您 的 着急"
    }
  ],
  "UserSumm": [
    "用户询问改密码除手机验证外的其他方法。",
    "用户询问密码登录后，怎样可以不用手机号验证。",
    "用户询问不验证是否可以买东西。"
  ],
  "AgentSumm": [
    "客服让用户提供号码，交给专员回电解决。",
    "客服帮助反馈用户的问题给专员。",
    "客服请用户耐心等待。"
  ],
  "FinalSumm": [
    "用户询问改密码除手机验证外的其他方法。",
    "客服让用户提供号码，交给专员回电解决。",
    "用户询问密码登录后，怎样可以不用手机号验证。",
    "客服帮助反馈给专员。",
    "用户询问不验证是否可以买东西。",
    "客服请用户耐心等待。"
  ]
}
```

## 完整原文样例：train.json 第一项

```json
{
  "DialogueID": 3559,
  "QRole": "用户",
  "QA": [
    {
      "QueSumm": "用户询问地址写错怎么处理。",
      "AnsSummShort": "客服回答客户提供咨询订单，在确认后发现商品暂未付款，客户修改地址即可。",
      "AnsSummLong": "客服回答客户提供咨询订单，在确认后发现商品暂未付款，客户修改地址即可。",
      "QueSummUttIDs": [
        2
      ],
      "AnsSummShortUttIDs": [
        3,
        6,
        7
      ],
      "AnsSummLongUttIDs": [
        3,
        6,
        7
      ],
      "QASumm": "用户询问地址写错怎么处理。客服回答客户提供咨询订单，在确认后发现商品暂未付款，客户修改地址即可。",
      "intent": "修改订单"
    },
    {
      "QueSumm": "用户询问如果取消订单，使用的券能否退回。",
      "AnsSummShort": "客服回答可以退回。在客户取消订单重新拍后，告知用户优惠券京东都已返还。",
      "AnsSummLong": "客服回答如果取消订单，使用的券可以退回。在客户取消订单重新拍后，告知用户优惠券京东都已返还。",
      "QueSummUttIDs": [
        9
      ],
      "AnsSummShortUttIDs": [
        16,
        10
      ],
      "AnsSummLongUttIDs": [
        16,
        9,
        10
      ],
      "QASumm": "用户询问如果取消订单，使用的券能否退回。客服回答可以退回。在客户取消订单重新拍后，告知用户优惠券京东都已返还。",
      "intent": "优惠券退回"
    }
  ],
  "Session_id": "13f09c952fc34164e4e511074c74bcd3",
  "Dialogue": [
    {
      "speaker": "Q",
      "turn": 0,
      "utterance": "配送 咨询"
    },
    {
      "speaker": "A",
      "turn": 1,
      "utterance": "亲亲 ， 有 什么 问题 我 可以 帮 您 处理 或 解决 的 呢 ? "
    },
    {
      "speaker": "Q",
      "turn": 2,
      "utterance": "地址 写错 了"
    },
    {
      "speaker": "A",
      "turn": 3,
      "utterance": "您好 ， APP 端 麻烦 您 点击 对话框 右下角 的 “ + ” ， 点击 “ 订单 ” 后 ， 选择 一下 您 需要 咨询 的 订单 哦 ， PC 端 在 我 的 订单 中 查看 ， 还 请 您 点击 复制 给 我 一下 哈 ， 小妹 这边 帮 您 查询 哦 ~"
    },
    {
      "speaker": "A",
      "turn": 4,
      "utterance": "亲亲   您 的 哪个 订单 写错 地址 了 呀"
    },
    {
      "speaker": "Q",
      "turn": 5,
      "utterance": "[订单编号]"
    },
    {
      "speaker": "A",
      "turn": 6,
      "utterance": "您 这个 还 没有 付款 哦"
    },
    {
      "speaker": "A",
      "turn": 7,
      "utterance": "您 修改 下 地址 就 可以 了 哦"
    },
    {
      "speaker": "A",
      "turn": 8,
      "utterance": "订单 状态 等待 付款"
    },
    {
      "speaker": "Q",
      "turn": 9,
      "utterance": "如果 我 现在 取消 订单 ， 我用 的 劵 还 可以 退回 吗"
    },
    {
      "speaker": "A",
      "turn": 10,
      "utterance": "当然 可以 的 呀"
    },
    {
      "speaker": "Q",
      "turn": 11,
      "utterance": "好 的 ， 我 从 新 拍"
    },
    {
      "speaker": "A",
      "turn": 12,
      "utterance": "会 退回 的 呢"
    },
    {
      "speaker": "A",
      "turn": 13,
      "utterance": "嗯 啦"
    },
    {
      "speaker": "A",
      "turn": 14,
      "utterance": "取消 订单 哦"
    },
    {
      "speaker": "A",
      "turn": 15,
      "utterance": "已经 退 了 哦"
    },
    {
      "speaker": "A",
      "turn": 16,
      "utterance": "您 的 优惠券 京豆 都 已经 返还"
    },
    {
      "speaker": "A",
      "turn": 17,
      "utterance": "您 注意 查收 哈"
    },
    {
      "speaker": "Q",
      "turn": 18,
      "utterance": "好 得"
    },
    {
      "speaker": "A",
      "turn": 19,
      "utterance": "请问 还有 其他 还 可以 帮到 您 的 吗 ?"
    },
    {
      "speaker": "Q",
      "turn": 20,
      "utterance": "没 了 ， 谢谢您"
    },
    {
      "speaker": "A",
      "turn": 21,
      "utterance": "这些 都 是 我们 的 分内之事 ， 您 别 客气 啦 ， 我 也 很 高兴 能 帮 您 这样 有 包容心 、 有 亲和力 的 人 解决问题 呢 ~"
    },
    {
      "speaker": "A",
      "turn": 22,
      "utterance": "感谢您 对 京东 的 支持 ， 祝您 生活 愉快 "
    }
  ],
  "UserSumm": [
    "用户询问地址写错怎么处理。",
    "用户询问如果取消订单，使用的券能否退回。"
  ],
  "AgentSumm": [
    "客服回答客户提供咨询订单，在确认后发现商品暂未付款，客户修改地址即可。",
    "客服回答如果取消订单，使用的券可以退回。在客户取消订单重新拍后，告知用户优惠券京东都已返还。"
  ],
  "FinalSumm": [
    "用户询问地址写错怎么处理。",
    "客服回答客户提供咨询订单，在确认后发现商品暂未付款，客户修改地址即可。",
    "用户询问如果取消订单，使用的券能否退回。",
    "客服回答可以退回。在客户取消订单重新拍后，告知用户优惠券京东都已返还。"
  ]
}
```

## 完整原文样例：val.json 第一项

```json
{
  "DialogueID": 4649,
  "QRole": "用户",
  "QA": [
    {
      "QueSumm": "用户询问可否帮忙改站点。",
      "AnsSummShort": "客服回答暂时无法修改。",
      "AnsSummLong": "客服回答暂时无法修改。",
      "QueSummUttIDs": [
        3,
        4,
        5
      ],
      "AnsSummShortUttIDs": [
        23
      ],
      "AnsSummLongUttIDs": [
        23
      ],
      "QASumm": "用户询问可否帮忙改站点。客服回答暂时无法修改。",
      "intent": "订单签收异常"
    },
    {
      "QueSumm": "用户询问是否需要转站。",
      "AnsSummShort": "客服回答配送师傅会联系用户进行协商。",
      "AnsSummLong": "客服回答配送师傅会联系用户进行协商。",
      "QueSummUttIDs": [
        26
      ],
      "AnsSummShortUttIDs": [
        29
      ],
      "AnsSummLongUttIDs": [
        29
      ],
      "QASumm": "用户询问是否需要转站。客服回答配送师傅会联系用户进行协商。",
      "intent": "联系配送"
    },
    {
      "QueSumm": "用户要求客服帮忙备注。",
      "AnsSummShort": "客服回答请放心已经备注。",
      "AnsSummLong": "客服回答请放心已经备注。",
      "QueSummUttIDs": [
        40
      ],
      "AnsSummShortUttIDs": [
        43,
        44
      ],
      "AnsSummLongUttIDs": [
        43,
        44
      ],
      "QASumm": "用户要求客服帮忙备注。客服回答请放心已经备注。",
      "intent": "下单备注"
    }
  ],
  "Session_id": "d60c792b1634b3b3dded8347f070abe2",
  "Dialogue": [
    {
      "speaker": "Q",
      "turn": 0,
      "utterance": "咨询 订单号 : [订单编号]"
    },
    {
      "speaker": "Q",
      "turn": 1,
      "utterance": "在 吗"
    },
    {
      "speaker": "A",
      "turn": 2,
      "utterance": "请问 您 是 咨询 之前 的 问题 还是 有 其他 的 问题 需要 处理 呢 ?"
    },
    {
      "speaker": "Q",
      "turn": 3,
      "utterance": "我 的 这个 单子 ， 站点 错 了"
    },
    {
      "speaker": "Q",
      "turn": 4,
      "utterance": "怎么办"
    },
    {
      "speaker": "Q",
      "turn": 5,
      "utterance": "能 不能 帮忙 说 下 啊"
    },
    {
      "speaker": "A",
      "turn": 6,
      "utterance": "您 是 申请 售后"
    },
    {
      "speaker": "A",
      "turn": 7,
      "utterance": "送到 自提点"
    },
    {
      "speaker": "A",
      "turn": 8,
      "utterance": "站点 选择 错误"
    },
    {
      "speaker": "A",
      "turn": 9,
      "utterance": "对 吗 ?"
    },
    {
      "speaker": "Q",
      "turn": 10,
      "utterance": "不是 ， 正确 站点 是 天宁 站"
    },
    {
      "speaker": "Q",
      "turn": 11,
      "utterance": "我 这边 显示 是 其他 的 站点"
    },
    {
      "speaker": "A",
      "turn": 12,
      "utterance": "本来 想 给 您 带来 阳光 般的 服务 ， 却 不 小心 给 了 您 一片 乌云 ， 确实 是 很 抱歉 哦 ， 您 咨询 的 问题 ， 我 立即 为 您 查看 ， 请 您 稍等一下 哦"
    },
    {
      "speaker": "Q",
      "turn": 13,
      "utterance": "哦 不 对 ， 是 青龙 站点"
    },
    {
      "speaker": "A",
      "turn": 14,
      "utterance": "[站点]   您 的 订单 已经 到达 [站点] ， 订单 共计 [数字] 个 包裹 ， 为 便于 您 签收 ， 待 所有 包裹 到 齐后 ， 我们 会为 您 一次 派送 ， 请 注意 查收 。"
    },
    {
      "speaker": "A",
      "turn": 15,
      "utterance": "您 是 指 在 这个 站点 对 吗 ?"
    },
    {
      "speaker": "Q",
      "turn": 16,
      "utterance": "不是 这个"
    },
    {
      "speaker": "Q",
      "turn": 17,
      "utterance": "[订单编号]"
    },
    {
      "speaker": "Q",
      "turn": 18,
      "utterance": "我 说 的 是 这个 订单"
    },
    {
      "speaker": "A",
      "turn": 19,
      "utterance": "好 的 呢"
    },
    {
      "speaker": "A",
      "turn": 20,
      "utterance": "您 是 需要 选择 天宁 站"
    },
    {
      "speaker": "A",
      "turn": 21,
      "utterance": "对 吗 ?"
    },
    {
      "speaker": "Q",
      "turn": 22,
      "utterance": "青龙 站"
    },
    {
      "speaker": "A",
      "turn": 23,
      "utterance": "这个 暂时 无法 修改 的 哦"
    },
    {
      "speaker": "A",
      "turn": 24,
      "utterance": "给 您 带来 不便 ， 还 请 您 见谅  "
    },
    {
      "speaker": "Q",
      "turn": 25,
      "utterance": "那 咋办"
    },
    {
      "speaker": "Q",
      "turn": 26,
      "utterance": "到时候 通知 道 你们 转 吗"
    },
    {
      "speaker": "A",
      "turn": 27,
      "utterance": "您 距离 这个 站点 在 [数字] 米 范围 之内 吗 ?"
    },
    {
      "speaker": "Q",
      "turn": 28,
      "utterance": "不 知道 啊"
    },
    {
      "speaker": "A",
      "turn": 29,
      "utterance": "这个 请 您 放心     到时候 配送 师傅 会 联系 您 进行 协商 的"
    },
    {
      "speaker": "Q",
      "turn": 30,
      "utterance": "好 吧"
    },
    {
      "speaker": "A",
      "turn": 31,
      "utterance": "您 的 订单 联系方式 可以 联系 到   您 吗 ?"
    },
    {
      "speaker": "Q",
      "turn": 32,
      "utterance": "可以 的"
    },
    {
      "speaker": "A",
      "turn": 33,
      "utterance": "好 的 呢"
    },
    {
      "speaker": "A",
      "turn": 34,
      "utterance": "请 您 放心"
    },
    {
      "speaker": "Q",
      "turn": 35,
      "utterance": "谢谢 妹子"
    },
    {
      "speaker": "Q",
      "turn": 36,
      "utterance": "服务态度 不错"
    },
    {
      "speaker": "A",
      "turn": 37,
      "utterance": "这 妹子 可以 帮 您 进行 跟进"
    },
    {
      "speaker": "Q",
      "turn": 38,
      "utterance": "好 的 ， 谢谢"
    },
    {
      "speaker": "A",
      "turn": 39,
      "utterance": "跟 配送 师傅 联系 给 您 安排 转 站点 给 您 配送"
    },
    {
      "speaker": "Q",
      "turn": 40,
      "utterance": "你 帮 我 备注 下 哦"
    },
    {
      "speaker": "Q",
      "turn": 41,
      "utterance": "别忘了"
    },
    {
      "speaker": "A",
      "turn": 42,
      "utterance": "可能 配送 师傅 不会 联系 您 回 直接 给 您 安排 转 站点 配送 的 呢"
    },
    {
      "speaker": "A",
      "turn": 43,
      "utterance": "妹子 会 帮 您 备注 的 哦"
    },
    {
      "speaker": "A",
      "turn": 44,
      "utterance": "请 您 放心"
    },
    {
      "speaker": "Q",
      "turn": 45,
      "utterance": "好"
    },
    {
      "speaker": "A",
      "turn": 46,
      "utterance": "请问 还有 其他 还 可以 帮到 您 的 吗 ?"
    }
  ],
  "UserSumm": [
    "用户询问可否帮忙改站点。",
    "用户询问是否需要转站。",
    "用户要求客服帮忙备注。"
  ],
  "AgentSumm": [
    "客服回答暂时无法修改。",
    "客服回答配送师傅会联系用户进行协商。",
    "客服回答请放心已经备注。"
  ],
  "FinalSumm": [
    "用户询问可否帮忙改站点。",
    "客服回答暂时无法修改。",
    "用户询问是否需要转站。",
    "客服回答配送师傅会联系用户进行协商。",
    "用户要求客服帮忙备注。",
    "客服回答请放心已经备注。"
  ]
}
```


## Delta：ADR-013–015

原文样例保留不变。当前 prompt 历史和目标仅去掉中文字符之间的横向分词空格，英文单词、数字间空白和换行保留。摘要不再凭“最后”删除；“最后请客服帮助取消订单”保留。新增删除“可以接收发票复印件”“不再退货”“已经退款了”等会话内结果；逐条核对覆盖520段摘要，规则不适用时使用 config/summary_overrides.json，逐ID核对说明在 config/summary_reviews.json 和两目录 summary_audit.json。不使用模型API做摘要判断。10013只标moderated，不重试，两侧从配对指标剔除；历史仍保留真实前文。首轮编号披露：真人含[数字]或[订单编号]，模拟器含上述占位或连续6位以上数字；不是有效订单号或真实性判断。
