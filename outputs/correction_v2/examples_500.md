# 固定规则抽取案例

按对话ID和轮次升序，取前10个基线与combined至少一个标签不同的轮次；不是额外标注。

## 0:2

客服：您好

- human：我这单价格保护是不是已经申请成功了；判断={'topic': 'unclear', 'emotion': False}
- baseline：昨天买的鞋子今天降价了，价保申请是不是已经成功了？；判断={'topic': 'unclear', 'emotion': False}
- situation：我的订单申请价格保护了吗？；判断={'topic': 'unclear', 'emotion': False}
- structure：你好；判断={'topic': 'unclear', 'emotion': False}
- combined：我想确认一下我的订单价格保护是不是已经申请成功了。 / 另外请问退款大概什么时候到账？ / 还有，我昨晚付清的白条欠款，退款会退到哪里？；判断={'topic': 'continue', 'emotion': False}

## 0:8

客服：银行卡，亲

- human：谢[姓名]；判断={'topic': 'close', 'emotion': False}
- baseline：嗯嗯，知道了，谢谢。；判断={'topic': 'close', 'emotion': False}
- situation：哦哦，原来是这样 / 我还以为白条付清后退款就只能退到别的地方呢；判断={'topic': 'close', 'emotion': False}
- structure：退到银行卡需要多久啊 / 真的这么简单吗？；判断={'topic': 'continue', 'emotion': True}
- combined：要等到白条出账单吗 / 还是今天就能退到银行卡；判断={'topic': 'continue', 'emotion': False}

## 40:2

客服：有什么问题我可以帮您处理或解决呢?

- human：之前买的魔法世家面膜，买回来一直没有用，今天拿出来，瓶子盖子一直打不开这是什么情况面膜我也用不了；判断={'topic': 'unclear', 'emotion': True}
- baseline：在的，我买个瓶子，盖子一直打不开，根本用不了，你这商品是不是有问题啊，给我换货吧；判断={'topic': 'continue', 'emotion': True}
- situation：我买的这个瓶子的盖子怎么都打不开，用了好多方法都不行，能帮我换个货吗？；判断={'topic': 'continue', 'emotion': False}
- structure：瓶子盖子打不开，换货；判断={'topic': 'continue', 'emotion': False}
- combined：你好，这个瓶子盖子我打不开，想换个货；判断={'topic': 'unclear', 'emotion': False}

## 40:4

客服：亲爱哒，这边应该是空气温度比较高凝固了呢

- human：也应该不是感觉里面有东西卡着，怎么拧也拧不开怎么解决我现在用也用不了；判断={'topic': 'continue', 'emotion': False}
- baseline：不管它是什么情况，我现在想换货可以吗？；判断={'topic': 'continue', 'emotion': True}
- situation：无语了都 / 那怎么弄啊 / 我要换货；判断={'topic': 'continue', 'emotion': True}
- structure：那怎么办，换货吗？ / 这太麻烦了，我急着用！；判断={'topic': 'continue', 'emotion': True}
- combined：是封的太死了吗？ / 能不能给我换个货；判断={'topic': 'continue', 'emotion': False}

## 40:6

客服：这边您是想要退货吗亲爱哒

- human：主要现在也用不了给我换个吧；判断={'topic': 'continue', 'emotion': False}
- baseline：不是我的原因吧 ，我现在想退货可以吧；判断={'topic': 'continue', 'emotion': True}
- situation：能换货吗；判断={'topic': 'continue', 'emotion': False}
- structure：是的，帮我换货吧。 / 我要重新发一个。 / 这个盖子不行。；判断={'topic': 'continue', 'emotion': False}
- combined：换货 / 盖子打不开 / 没法用；判断={'topic': 'continue', 'emotion': False}

## 76:3

客服：您好，APP端麻烦您点击对话框右下角的“+”，点击“订单”后，选择一下您需要咨询的订单哦，PC端在我的订单中查看，还请您点击复制给我一下哈，小妹这边帮您查询哦~

- human：我已经和店家申请了退货咨询订单号:[订单编号]商品ID:27104639434；判断={'topic': 'unclear', 'emotion': False}
- baseline：852116158783；判断={'topic': 'unclear', 'emotion': False}
- situation：咨询订单号 : 123456789   商品 ID : 987654321；判断={'topic': 'unclear', 'emotion': False}
- structure：鞋子开胶，商家不管了。 / 白条还款了钱退哪里？；判断={'topic': 'continue', 'emotion': False}
- combined：咨询订单号： [订单编号] 白条支付的能退吗？；判断={'topic': 'continue', 'emotion': False}

## 76:5

客服：您说

- human：穿了一个月不到出现质量问题已经和商家协调退货但是申请售后过了时效；判断={'topic': 'continue', 'emotion': False}
- baseline：我买过时效了，想让客服帮忙申请一下。这鞋子穿一天就坏了，质量太差了！；判断={'topic': 'continue', 'emotion': True}
- situation：就是这双黑色的鞋子出现了质量问题。和商家商量过了，但是退货申请已经过了时效，可以帮我再申请一下嘛？如果可以退款的话，会退到哪里呢？我之前是用白条支付的，并且已经还款了。；判断={'topic': 'continue', 'emotion': False}
- structure：这鞋有问题想退货，但过了申请时间。能帮我申请下吗？；判断={'topic': 'continue', 'emotion': False}
- combined：能帮我特例申请一下吗 / 退款会退到哪里；判断={'topic': 'continue', 'emotion': False}

## 76:7

客服：[订单编号]是这一单吗具体哪一双呢

- human：对的；判断={'topic': 'unclear', 'emotion': False}
- baseline：对，就是这单，黑色的那双。；判断={'topic': 'unclear', 'emotion': False}
- situation：对，就是这个订单，黑色那双。；判断={'topic': 'unclear', 'emotion': False}
- structure：对的。；判断={'topic': 'unclear', 'emotion': False}
- combined：是这单呀，就是那双黑色的鞋子 / 要申请退货；判断={'topic': 'continue', 'emotion': False}

## 76:10

客服：这边系统给您的手机发送了一个六位数的验证码，麻烦您提供一下呢

- human：[数字]；判断={'topic': 'unclear', 'emotion': False}
- baseline：123456；判断={'topic': 'unclear', 'emotion': False}
- situation：[数字]；判断={'topic': 'unclear', 'emotion': False}
- structure：是的，收到，我看一下短信；判断={'topic': 'unclear', 'emotion': False}
- combined：你好，我这边之前用白条支付的，并且已经还款，现在问题是退款会退到哪里呢？；判断={'topic': 'continue', 'emotion': False}

## 76:14

客服：返还到您白条的

- human：但是我白条已经还款了啊；判断={'topic': 'continue', 'emotion': False}
- baseline：但是我已经还款了，这个钱会退到哪里呢？；判断={'topic': 'continue', 'emotion': False}
- situation：那我还款了的，也会退到白条吗？；判断={'topic': 'continue', 'emotion': False}
- structure：好的但是我已经还款了；判断={'topic': 'continue', 'emotion': False}
- combined：明白了；判断={'topic': 'close', 'emotion': False}

