# 固定规则抽取案例

按对话ID和轮次升序，取前10个基线与combined至少一个标签不同的轮次；不是额外标注。

## 1610:2

客服：这个是比较完善的呢

- human：那比如我们用其他打印软件设置打印模板快递说没有显示三段码跟水印不行这个只有大头笔跟集包地；判断={'topic': 'continue', 'emotion': False}
- baseline：如果我想用别的模板，有没有其他选择呢？；判断={'topic': 'continue', 'emotion': False}
- situation：如果达不到快递标准能发吗；判断={'topic': 'close', 'emotion': False}
- structure：如果达不到快递标准能发吗；判断={'topic': 'close', 'emotion': False}
- combined：达不到快递标准的话，能发吗？；判断={'topic': 'close', 'emotion': False}

## 1610:6

客服：您看可以吗

- human：好吧那就是目前就是不行；判断={'topic': 'close', 'emotion': False}
- baseline：那我的快递如果达不到标准，你们还能发吗？；判断={'topic': 'continue', 'emotion': False}
- situation：是的，菜鸟那边用起来没问题，为什么你们京东这边就不行呢？；判断={'topic': 'continue', 'emotion': True}
- structure：那你们达不到标准能不能发呀；判断={'topic': 'continue', 'emotion': False}
- combined：可以 / 这样也能解决问题；判断={'topic': 'close', 'emotion': False}

## 1633:3

客服：多下单了?

- human：是；判断={'topic': 'unclear', 'emotion': False}
- baseline：对，不小心点多了，这个就不需要了。；判断={'topic': 'continue', 'emotion': False}
- situation：嗯，是的，麻烦帮我取消其中一个订单吧，谢谢！；判断={'topic': 'continue', 'emotion': False}
- structure：是的，多下了。；判断={'topic': 'unclear', 'emotion': False}
- combined：对，多买了；判断={'topic': 'unclear', 'emotion': False}

## 2403:5

客服：有吗?

- human：没有；判断={'topic': 'continue', 'emotion': True}
- baseline：哦好的，我去快递面单下面找找，谢谢。；判断={'topic': 'close', 'emotion': False}
- situation：没找到呀；判断={'topic': 'unclear', 'emotion': False}
- structure：两个都找了真没有；判断={'topic': 'unclear', 'emotion': False}
- combined：没看到呀；判断={'topic': 'unclear', 'emotion': False}

## 2543:5

客服：还请您能耐心等待一下哦

- human：好的谢谢；判断={'topic': 'close', 'emotion': False}
- baseline：好的，谢谢 / 等退款到了我就重新下单；判断={'topic': 'close', 'emotion': False}
- situation：一般多久到账；判断={'topic': 'continue', 'emotion': False}
- structure：好的，谢谢。 / 那我再重新买一个；判断={'topic': 'close', 'emotion': False}
- combined：我点退款是要把东西退掉， / 不是只想改地址啊。；判断={'topic': 'continue', 'emotion': True}

## 2543:6

客服：请问还有其他还可以帮到您的吗?

- human：没什么了；判断={'topic': 'close', 'emotion': False}
- baseline：没有了，谢谢你的耐心解答。；判断={'topic': 'close', 'emotion': False}
- situation：那请问退款多久可以到账啊？；判断={'topic': 'continue', 'emotion': False}
- structure：我想修改订单时总提示错误是怎么回事？ / 是必须退货才能更改地址吗？ / 还是说有其它方式？；判断={'topic': 'close', 'emotion': False}
- combined：那修改地址错误是为啥呀  / 能帮我看看咋回事吗 / 我有点搞不懂；判断={'topic': 'continue', 'emotion': False}

## 2732:3

客服：这个订单么

- human：处理换货太久了是的；判断={'topic': 'unclear', 'emotion': True}
- baseline：是的 ， 就是上面那个订单编号。我看换货还要等那么久，不如直接退货，我再重新下单好了。；判断={'topic': 'continue', 'emotion': False}
- situation：对，就是这个订单。这个换货时间也太长了，我还是直接退货吧，然后我重新下单。；判断={'topic': 'continue', 'emotion': True}
- structure：对的；判断={'topic': 'unclear', 'emotion': False}
- combined：对 ，是的 ，这样来回检测太费时间了 。我想直接退货 ，然后重新下单买一个新的。；判断={'topic': 'continue', 'emotion': True}

## 2732:4

客服：这边退换货都是需要寄回售后检测的哦

- human：应该是今天取件没事，主要是我想重新下单；判断={'topic': 'continue', 'emotion': False}
- baseline：我知道啊，但是换货时间太久了，我还不如直接退掉重新下单。；判断={'topic': 'continue', 'emotion': True}
- situation：我已经取件了啊 / 流程需要很久吗 ?；判断={'topic': 'continue', 'emotion': False}
- structure：那我直接申请退货 然后重新下单可以嘛；判断={'topic': 'continue', 'emotion': False}
- combined：直接退货不行么；判断={'topic': 'continue', 'emotion': False}

## 2732:5

客服：这边给您备注一下

- human：机子是昨天收到就有问题的，可以送回慢慢检测，然后退货。这样我可以买其它牌子或者重新下单；判断={'topic': 'continue', 'emotion': False}
- baseline：好的，谢谢。麻烦快一点处理，谢谢。；判断={'topic': 'continue', 'emotion': False}
- situation：好的，谢谢。 / 就按退货处理吧，我要重新下单。；判断={'topic': 'continue', 'emotion': False}
- structure：好的谢谢；判断={'topic': 'close', 'emotion': False}
- combined：好的；判断={'topic': 'close', 'emotion': False}

## 2909:6

客服：什么时间下班

- human：七点；判断={'topic': 'unclear', 'emotion': False}
- baseline：我一般五点下班，比较忙不一定能及时看手机，能不能约周末取件啊？；判断={'topic': 'continue', 'emotion': False}
- situation：六点下班，你们不能在六点以后来取吗？；判断={'topic': 'continue', 'emotion': False}
- structure：六点下班；判断={'topic': 'unclear', 'emotion': False}
- combined：晚上6点下班；判断={'topic': 'unclear', 'emotion': False}

