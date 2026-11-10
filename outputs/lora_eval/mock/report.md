# LoRA evaluation

Mock: True; paired turns: 4299; exclusions: {'inherited_P2_ineligible': 411}
PPI: frozen 500/100 dialogue design, 2000 paired dialogue bootstrap; gaps = simulator − human. P2 numbers retain their original support.
ADR-018 adoption was post-hoc (one preregistered efficiency cell failed).

|method|condition|metric|gap|95% CI|
|---|---|---|---:|---|
|ppi|base|continue|0.000000|[0.0, 0.0]|
|ppi|base|emotion|0.000000|[0.0, 0.0]|
|ppi|lora|continue|0.000000|[0.0, 0.0]|
|ppi|lora|emotion|0.000000|[0.0, 0.0]|
|ppi|P2 baseline|continue|0.256983|[0.22555865921787704, 0.28724394785847296]|
|ppi|P2 decision|continue|0.219739|[0.19064245810055863, 0.24650837988826824]|
|ppi|P2 oracle|continue|0.143622|[0.11918063314711352, 0.16806331471135938]|
|ppi|P2 baseline|emotion|0.061685|[0.04073556797020485, 0.08263500931098695]|
|ppi|P2 decision|emotion|0.056564|[0.03445065176908753, 0.0798417132216015]|
|ppi|P2 oracle|emotion|0.068203|[0.04958100558659216, 0.08566108007448789]|
|spark_only|base|continue|0.000000|None|
|spark_only|base|emotion|0.000000|None|
|spark_only|lora|continue|0.000000|None|
|spark_only|lora|emotion|0.000000|None|
|spark_only|P2 baseline|continue|0.277933|None|
|spark_only|P2 decision|continue|0.238361|None|
|spark_only|P2 oracle|continue|0.185521|None|
|spark_only|P2 baseline|emotion|0.067505|None|
|spark_only|P2 decision|emotion|0.064711|None|
|spark_only|P2 oracle|emotion|0.074022|None|
|gemini_only|base|continue|0.000000|[0.0, 0.0]|
|gemini_only|base|emotion|0.000000|[0.0, 0.0]|
|gemini_only|lora|continue|0.000000|[0.0, 0.0]|
|gemini_only|lora|emotion|0.000000|[0.0, 0.0]|
|gemini_only|P2 baseline|continue|0.247905|[0.21066108007448792, 0.28398510242085656]|
|gemini_only|P2 decision|continue|0.209497|[0.16527001862197394, 0.25139664804469275]|
|gemini_only|P2 oracle|continue|0.126862|[0.09427374301675978, 0.15945065176908751]|
|gemini_only|P2 baseline|emotion|0.070996|[0.036080074487895714, 0.10707635009310987]|
|gemini_only|P2 decision|emotion|0.061685|[0.027932960893854747, 0.09660148975791434]|
|gemini_only|P2 oracle|emotion|0.065177|[0.03605097765363127, 0.09660148975791434]|
|Gemini full (P2 v2; N=4300)|baseline|continue|0.262791|[0.2437661663771885, 0.2803178975173131]|
|Gemini full (P2 v2; N=4300)|situation|continue|0.234651|[0.21620535445299574, 0.252225550263729]|
|Gemini full (P2 v2; N=4300)|structure|continue|0.243256|[0.22310020476545045, 0.262355673455617]|
|Gemini full (P2 v2; N=4300)|combined|continue|0.269535|[0.24982059128531708, 0.2895626366760387]|
|Gemini full (P2 v2; N=4300)|baseline|emotion|0.047442|[0.03258150122140796, 0.06245668380026998]|
|Gemini full (P2 v2; N=4300)|situation|emotion|0.010000|[-0.002782947492908715, 0.023508247736853814]|
|Gemini full (P2 v2; N=4300)|structure|emotion|0.016512|[0.003681825025241705, 0.030220441001345667]|
|Gemini full (P2 v2; N=4300)|combined|emotion|0.002326|[-0.011112468015817634, 0.017419533625057264]|

{"logical_inputs": 2481, "reusable_unique": 858, "new_unique": 768, "estimated_usd": 1.0548034608150445, "retry_inclusive_estimated_upper_usd": 3.1644103824451335, "cost_basis": "P2 measured USD/judgment; 3 transient attempts; estimate, not a tariff guarantee", "billing_stop_usd": 7.0}

Cost estimates use historical average; they are not guaranteed provider price caps.
