#!/usr/bin/env python3
"""
Strategy Academy: one lesson per model the dashboard trades.

Served by ict_app.py at  /learn   (deep links like /learn#pa_retest).
Or write a standalone copy:   python ict_learn.py      -> ict_learn.html
Educational content. Not financial advice. Demo-trade everything before risking money.
"""
import json

# d = diagram: p = price path (0-100, higher = higher price), z = zones [x1, x2, ylo, yhi, label, buy|sell|neu],
#              m = marks [x, text, up|down], l = dashed levels [y, label]
LESSONS = [
 # ------------------------------------------------------------------ foundations
 dict(id="sessions", g="Foundations", t="Killzones and sessions", tag="Timing",
  sum="Price does not move evenly through the day. Four windows produce most clean setups.",
  why="Orders concentrate when London and New York open. Asia builds a range, London often raids one side of it, and New York usually decides the day's direction.",
  rules=["Asia 20:00-00:00 NY (03:00-07:00 Kampala): mark its high and low. Do not trade the middle.",
         "London 02:00-05:00 NY (09:00-12:00 Kampala): watch for a raid of the Asia high or low.",
         "New York AM 07:00-10:00 NY (14:00-17:00 Kampala): the main window for entries.",
         "New York PM 13:30-16:00 NY (20:30-23:00 Kampala): secondary; continues or reverses the AM move.",
         "Silver Bullet hours: 03-04, 10-11 and 14-15 NY."],
  entry="Only take entries inside a killzone.", stop="Not session-specific: see Risk.", target="Opposite side of the Asia range, then the previous day's high or low.",
  mist=["Trading the middle of the Asia range.", "Forgetting that the Kampala offset grows from 7 to 8 hours when US daylight saving ends on 1 November."],
  dash="The header shows the New York clock and a killzone chip. Setups score +1 when they form inside a killzone.",
  chk=["It is 15:00 in Kampala (before 1 November). Which window is it?", "08:00 New York: the New York AM killzone."],
  d=dict(p=[50,50,52,49,51,50,64,44,42,55,68,80,86], z=[[0,5,46,55,"Asia range","neu"]], l=[[55,"Asia high"],[46,"Asia low"]],
         m=[[6,"London sweeps Asia high","up"],[7,"low of day","down"],[12,"New York run","up"]])),
 dict(id="liquidity", g="Foundations", t="Liquidity and sweeps", tag="Core idea",
  sum="Stop orders pile up above highs and below lows. Price is drawn to them, takes them, then often reverses.",
  why="Large players need many orders to fill size. Resting stops above equal highs or below equal lows are that supply. A sweep fills them, and the real move starts after.",
  rules=["Pools above price: previous day or week high, session highs, equal highs, obvious swing highs. Below price: the mirror.",
         "A sweep is a wick through the level that closes back inside. A close and hold beyond it is a breakout, not a sweep.",
         "Confirmation is displacement away from the level: strong candles in the opposite direction.",
         "The next opposing pool is your draw on liquidity, your target."],
  entry="Not an entry alone. A sweep is step one of every ICT model.", stop="Beyond the sweep's extreme.", target="The opposite liquidity pool.",
  mist=["Buying every low without waiting for displacement.", "Calling a breakout a sweep: if price holds beyond the level, the idea is dead."],
  dash="The chart draws previous-day, Asia and London highs and lows. Every ICT setup note starts with the sweep that created it.",
  chk=["Price wicks 2 pips above equal highs but closes back below. What is it?", "A buy-side sweep. Bearish only once displacement down follows."],
  d=dict(p=[60,66,62,67,63,68,62,73,56,46,40], l=[[68,"equal highs: buy stops"]], z=[],
         m=[[7,"sweep","up"],[9,"displacement","down"],[10,"draw: next low","down"]])),
 dict(id="mss", g="Foundations", t="Displacement, fair value gaps and structure shift", tag="Confirmation",
  sum="After a sweep you need proof the direction changed. Proof is displacement, a gap it leaves, and a break of structure.",
  why="Impulsive candles show aggressive, one-sided orders. They leave gaps (fair value gaps) that price often returns to, which gives a precise, low-risk entry.",
  rules=["Displacement: one or more large-bodied candles, clearly bigger than the recent average.",
         "Fair value gap (FVG): candle 1's high is below candle 3's low (bullish). The space between is unfilled.",
         "Market structure shift (MSS): a close beyond the last opposing swing (above the last lower high, for longs).",
         "Best signal: sweep, displacement, MSS, with an FVG left behind."],
  entry="Limit order at the FVG's midpoint (consequent encroachment).", stop="Beyond the FVG or the sweep's extreme.", target="Next liquidity pool; at least 2R.",
  mist=["Treating a small, overlapping gap as an FVG.", "Entering before the MSS closes."],
  dash="Notes quote the MSS time and the FVG range. Tiny gaps are filtered out using ATR.",
  chk=["Bullish FVG between candle 1's high 100.0 and candle 3's low 100.6. Where is the midpoint?", "100.3."],
  d=dict(p=[48,44,38,40,52,64,60,58,70,80], z=[[3,5,40,52,"FVG","buy"]], l=[[47,"last lower high"]],
         m=[[2,"sweep low","down"],[4,"displacement","up"],[5,"MSS","up"],[7,"entry in FVG","down"]])),
 dict(id="pd", g="Foundations", t="Premium, discount and order blocks", tag="Where to enter",
  sum="Buy cheap, sell expensive, relative to the current range, and prefer the candle that started the move.",
  why="Above the range's midpoint price is expensive (premium); below it is cheap (discount). Institutions accumulate in discount and distribute in premium.",
  rules=["Take the dealing range (swing low to swing high). The 50% line is equilibrium.",
         "Longs from discount, shorts from premium. A long from premium fights the range.",
         "Order block (OB): the last opposite candle before displacement (last down candle before a rally).",
         "An OB is valid until price closes through it. Its open or midpoint is the entry reference."],
  entry="At the OB or FVG inside discount (longs) or premium (shorts).", stop="Beyond the OB.", target="Opposing range extreme or liquidity pool.",
  mist=["Ignoring the half of the range you are in.", "Using an OB that price has already closed through."],
  dash="The bias card shows 'PREMIUM / DISCOUNT' for the 20-day range, and the top-down workflow refuses a 4H zone in the wrong half.",
  chk=["Range 100-110, price at 108. Premium or discount?", "Premium (above 105). Favour shorts."],
  d=dict(p=[30,44,58,70,86,76,64,56,46,52,66], z=[[0,10,58,90,"premium","sell"],[0,10,10,50,"discount","buy"]], l=[[58,"equilibrium"]],
         m=[[8,"buy from discount","down"]])),
 dict(id="risk", g="Foundations", t="Risk and trade management", tag="Survival",
  sum="No model matters if one bad run ends the account. Risk is fixed before the entry, never after.",
  why="Every model here loses often. A fixed small risk and a 1.5-3R reward keeps losses survivable and lets a few winners pay for many losers.",
  rules=["Risk 0.5-1% of the account per trade. Size = risk amount / stop distance.",
         "Minimum reward to risk 1.5. The dashboard drops anything below it.",
         "Daily loss cap: stop after 2-3 losses or 2% down.",
         "Skip high-impact news: CPI, FOMC, NFP. The news gate shows BLACKOUT 30 minutes either side.",
         "Move to breakeven after the first strong displacement or TP1. Never widen a stop."],
  entry="Only after the stop and size are known.", stop="At the invalidation point, not at a comfortable amount.", target="At least 1.5R; scale out at TP1.",
  mist=["Moving the stop 'to give it room'.", "Revenge trading after a loss.", "Sizing up because the setup score is high."],
  dash="Each card shows RR. Score is quality, not a reason to risk more.",
  chk=["Account 5,000, risk 1%, stop 25 points. Points value 1 per point per lot. Position?", "Risk 50; 50 / 25 = 2 lots."],
  d=dict(p=[50,52,50,53,51,54,52,58,66,74], z=[[3,9,44,50,"risk","sell"],[7,9,52,76,"reward 2R+","buy"]], l=[[51,"entry"]],
         m=[[9,"TP","up"]])),
 # ------------------------------------------------------------------ workflow
 dict(id="htf_bias", g="Workflow", t="Higher-timeframe bias", tag="Weekly / Daily / 4H",
  sum="Decide the direction before looking for entries. Lower timeframes give timing, higher timeframes give direction.",
  why="Trades with the higher-timeframe flow have more room to run and fewer failures. The bias card turns five observations into a score.",
  rules=["Score -5 to +5 from: last weekly candle, last daily candle, daily structure, 4H structure, price vs weekly open.",
         "+3 or more is a strong bullish bias; -3 or less strong bearish; near 0 means no edge.",
         "A close beyond the prior candle's range is displacement. A sweep that closes back inside is a rejection.",
         "Check premium/discount of the 20-day range and the nearest untaken liquidity (draw on liquidity).",
         "Invalidation: a daily close beyond the last daily swing."],
  entry="None. This step only picks the side.", stop="Bias is invalid beyond the daily swing.", target="The draw on liquidity shown on the card.",
  mist=["Trading against a strong bias on a 1m signal.", "Chasing longs while price is in premium."],
  dash="Bias tab: one card per pair with score, premium/discount, draw on liquidity, FVGs, invalidation and a plan line.",
  chk=["Bias +4 but price is in premium. What do you do?", "Stay long-only but wait for a pullback to discount."],
  d=dict(p=[30,36,34,44,42,54,52,64,62,74,72,84], z=[], l=[[56,"weekly open"]], m=[[3,"HH","up"],[4,"HL","down"],[11,"draw: previous high","up"]])),
 dict(id="topdown", g="Workflow", t="Top-down workflow", tag="4H > 1H > 1M",
  sum="One checklist from news to trigger. Every step must pass before the next one counts.",
  why="It stops you from taking a good-looking 1-minute signal in a bad context.",
  rules=["News gate: no high-impact release about to hit.",
         "Step 1, bias: Daily/Weekly direction.",
         "Step 2, 4H POI: unmitigated FVG or order block on the trade side, in the favourable half of the range, and price has tapped it.",
         "Step 3, 1H confirmation: sweep, displacement and MSS at that POI. It gives the refined zone and the OTE band.",
         "Step 4, 1M trigger: a scanner model in the bias direction, inside a killzone. Inside the 1H zone is better."],
  entry="The 1M model's limit order.", stop="Beyond the 1M sweep or zone.", target="1H/4H liquidity; partial at 50% of the range.",
  mist=["Skipping step 3 because the 1M looks great.", "Trading outside a killzone."],
  dash="Top-down tab: pick a pair; read the verdict (TRADE READY, WAIT, NO TRADE) and the exact step that is missing.",
  chk=["The 4H POI is tapped but sits in premium and bias is bullish. Verdict?", "WAIT: wrong half of the range."],
  d=dict(p=[40,46,52,48,42,36,38,50,60,66], z=[[3,6,34,44,"4H POI (discount)","buy"]], m=[[5,"1H sweep","down"],[7,"MSS","up"],[8,"1M trigger","down"]])),
 # ------------------------------------------------------------------ ICT
 dict(id="sweep_mss_fvg", g="ICT models", t="Sweep + MSS + FVG", tag="ICT · 1m-1h",
  sum="The core ICT sequence: liquidity raid, shift in structure, entry in the gap left behind.",
  why="The raid removes weak hands, displacement shows who is in control, and the FVG gives a tight entry with a clear invalidation.",
  rules=["A liquidity pool is swept: session low, previous day low, or a recent swing.",
         "Displacement closes beyond the last opposing swing (the MSS).",
         "The move leaves an FVG of meaningful size relative to ATR.",
         "Trade only in the direction of the higher-timeframe bias, inside a killzone."],
  entry="Limit at the FVG midpoint.", stop="Beyond the swept extreme.", target="Next liquidity pool (internal first, then external).",
  mist=["Entering without an MSS.", "Fighting the daily bias.", "Using FVGs smaller than the noise."],
  dash="ICT & SMC tab, model 'Sweep+MSS+FVG'. Card shows limit, SL, TP, RR, status (PENDING, FILLED) and the note naming the sweep.",
  chk=["Price swept the Asia low, then closed above the last lower high. What is that close?", "The MSS (market structure shift)."],
  d=dict(p=[56,52,48,44,38,52,62,56,60,72,80], z=[[5,7,50,58,"FVG","buy"]], l=[[44,"Asia low"]], m=[[4,"sweep","down"],[6,"MSS","up"],[7,"limit fills","down"],[10,"TP","up"]])),
 dict(id="ict_ob", g="ICT models", t="Order block", tag="ICT · 5m-1h",
  sum="Enter at the last opposite candle before displacement, after a sweep.",
  why="That candle marks where aggressive orders began. When price returns, unfilled orders there can defend the level again.",
  rules=["A sweep and a displacement move away from the zone.",
         "The OB is the last down candle before the up move (bullish case).",
         "It must be unmitigated: no close through it since.",
         "Prefer an OB in discount (longs) or premium (shorts)."],
  entry="Limit at the OB open (the dashboard uses the candle's open).", stop="Beyond the OB's far side.", target="Next liquidity pool; at least 1.5R.",
  mist=["Picking any down candle instead of the last one before the move.", "Entering an OB that has been closed through."],
  dash="ICT & SMC tab, model 'Order Block'.",
  chk=["When is an order block no longer valid?", "After price closes through it."],
  d=dict(p=[50,54,52,46,44,60,72,66,58,54,70,78], z=[[3,4,43,50,"order block","buy"]], m=[[5,"displacement","up"],[9,"return to OB","down"],[11,"TP","up"]])),
 dict(id="ote", g="ICT models", t="Optimal trade entry (OTE)", tag="ICT · 5m-1h",
  sum="After a displacement leg, buy the retracement between 62% and 79%.",
  why="Retracements that reach deep into the leg offer a better price and a smaller stop than shallow ones, while still respecting the structure.",
  rules=["A clear displacement leg after a sweep (low to high for longs).",
         "Measure the leg; the OTE band is the 62%-79% retracement. 70.5% is the sweet spot.",
         "Look for an FVG or OB inside the band for precision.",
         "Invalid if price closes beyond the leg's start."],
  entry="Limit inside the 62-79% band.", stop="Below the leg's start (the swept low).", target="Leg high, then beyond.",
  mist=["Measuring from the wrong swing.", "Buying a 40% retrace and calling it OTE."],
  dash="ICT & SMC tab, model 'OTE 62-79%'. The note gives the leg and the band.",
  chk=["Leg from 100 to 110. Where is 70.5% retracement?", "110 - 0.705 x 10 = 102.95."],
  d=dict(p=[40,36,30,48,66,82,72,66,64,74,88], z=[[5,9,64,72,"OTE 62-79%","buy"]], m=[[2,"leg start","down"],[5,"leg high","up"],[8,"entry","down"],[10,"TP","up"]])),
 dict(id="judas", g="ICT models", t="Judas swing", tag="ICT · 5m-15m",
  sum="A fake move at the session open that runs stops, then reverses in the real direction.",
  why="London and New York open with a false push against the day's true move. It hunts stops from the previous session.",
  rules=["Happens near the London (02:00 NY) or New York (07:00-08:30 NY) open.",
         "Price pushes beyond the overnight high or low, then reverses.",
         "Trade only with the higher-timeframe bias.",
         "Needs displacement back through the opening range."],
  entry="Limit at the FVG or OB left by the reversal.", stop="Beyond the Judas extreme.", target="The opposite side of the range, then the draw on liquidity.",
  mist=["Joining the false move.", "Entering before reversal displacement."],
  dash="ICT & SMC tab, model 'Judas Swing'.",
  chk=["London opens and pushes above the Asia high, then reverses hard. Bullish or bearish?", "Bearish (a buy-side sweep followed by displacement down)."],
  d=dict(p=[50,50,51,49,50,62,72,60,46,38,32], z=[[0,4,46,54,"Asia range","neu"]], l=[[54,"Asia high"]], m=[[6,"Judas high","up"],[8,"reversal","down"],[10,"TP","down"]])),
 dict(id="silver_bullet", g="ICT models", t="Silver Bullet", tag="ICT · 1m-5m",
  sum="A time-boxed model: the first clean FVG after a sweep inside a one-hour window.",
  why="Time and price meet. Inside these hours liquidity sweeps and clean displacement happen often enough to define a repeatable rule.",
  rules=["Windows (NY): 03:00-04:00, 10:00-11:00, 14:00-15:00.",
         "A nearby liquidity pool is swept.",
         "Displacement leaves an FVG in the direction of the bias.",
         "Target the next pool with at least 1.5R."],
  entry="Limit at the FVG, inside the window.", stop="Beyond the sweep or the FVG.", target="Opposing liquidity.",
  mist=["Trading it outside the window.", "No sweep before the gap."],
  dash="ICT & SMC tab, model 'Silver Bullet'. Not produced on the 1h timeframe: the window is only one bar there.",
  chk=["Valid Silver Bullet entry time in New York?", "10:00-11:00 (also 03-04 and 14-15)."],
  d=dict(p=[60,58,54,50,46,40,52,62,56,60,72], z=[[6,8,52,60,"FVG","buy"],[4,10,28,36,"","neu"]], l=[[46,"swept low"]], m=[[5,"sweep","down"],[8,"limit","down"],[10,"TP","up"]])),
 # ------------------------------------------------------------------ SMC
 dict(id="smc_bos", g="SMC models", t="BOS + order block", tag="SMC · 5m-1h",
  sum="Trend continuation: a break of structure, then entry at the order block that caused it.",
  why="When price closes beyond the latest swing in trend direction, the candle that launched the move is where continuation orders wait.",
  rules=["An established trend (higher highs and higher lows, for up).",
         "BOS: a close beyond the latest swing high in trend direction.",
         "Mark the OB candle that started the leg.",
         "OB must be unbroken."],
  entry="Limit at 50% of the OB.", stop="Beyond the OB.", target="The next swing or liquidity pool.",
  mist=["Treating a wick through the swing as a BOS: it needs a close.", "Chasing the BOS candle."],
  dash="Set strategy to SMC or Both. Model 'SMC BOS + OB'.",
  chk=["What makes a break of structure valid?", "A candle close beyond the swing, not just a wick."],
  d=dict(p=[40,52,46,58,52,66,60,74,68,82], z=[[5,6,56,62,"OB","buy"]], l=[[58,"swing high"]], m=[[5,"BOS","up"],[8,"retest OB","down"]])),
 dict(id="smc_choch", g="SMC models", t="CHoCH + order block", tag="SMC · 5m-1h",
  sum="Trend reversal: the first close against the prior trend, then entry at the order block.",
  why="A change of character is the earliest sign the trend may have flipped. Entering from the OB that caused it gives a defined risk.",
  rules=["A prior trend (down, for a bullish CHoCH).",
         "CHoCH: the first close above the last lower high.",
         "Mark the OB that launched the break.",
         "Better after a sweep and in discount; weaker alone."],
  entry="Limit at 50% of the OB.", stop="Beyond the OB.", target="Next opposing swing; at least 1.5R.",
  mist=["Calling every small break a CHoCH.", "Counter-trend entries against the daily bias."],
  dash="Model 'SMC CHoCH + OB'. Note says 'Structure flipped: first close against the prior trend.'",
  chk=["CHoCH or BOS: first close above a lower high in a downtrend?", "CHoCH."],
  d=dict(p=[80,70,74,62,66,52,56,44,58,66,76], z=[[7,8,44,50,"OB","buy"]], l=[[62,"lower high"]], m=[[8,"CHoCH","up"],[10,"TP","up"]])),
 # ------------------------------------------------------------------ SMT
 dict(id="smt_pairs", g="Confirmation", t="SMT divergence, FX and gold", tag="SMT",
  sum="Two correlated markets disagree at a liquidity level: one takes it, the other refuses.",
  why="The market that refuses is showing real strength at a low, or weakness at a high. The other market's raid is more likely a stop run than a trend change.",
  rules=["Pairs: EURUSD-GBPUSD, XAUUSD-EURUSD move together. EURUSD-USDJPY, GBPUSD-USDJPY, XAUUSD-USDJPY move opposite (the dashboard flips the second).",
         "Level SMT: sweep at previous-day, Asia or London highs and lows. Swing SMT: against the recent range.",
         "Bullish: one makes a new low, the other holds. Bearish: the same at highs.",
         "Score 1-4: +1 reclaimed, +1 killzone, +1 key level."],
  entry="SMT never enters. Use it to confirm a sweep in a model.", stop="Per the model.", target="Per the model.",
  mist=["Trading SMT as a standalone signal.", "Forgetting inverse pairs: USDJPY failing a high is bullish for EURUSD."],
  dash="SMT tab, FX/gold. Each card gives which pair took and which held, plus a buy/sell lean for each.",
  chk=["EURUSD makes a new low, GBPUSD does not. Bullish or bearish?", "Bullish SMT. The market that held (GBPUSD) is the strong one."],
  d=dict(p=[60,56,52,48,44,38,46,54,60], z=[], l=[[44,"previous low"]], m=[[5,"A sweeps low","down"],[6,"B held above its low","up"],[8,"reversal","up"]])),
 dict(id="smt_index", g="Confirmation", t="Index SMT and triple SMT", tag="SMT · NQ ES YM",
  sum="The classic index form: NQ, ES and YM should move together. When they split at a level, the split is information.",
  why="Three highly correlated futures make divergence clean. If two sweep a low and one holds, the holder is leading.",
  rules=["Pairwise: NQ-ES, NQ-YM, ES-YM.",
         "Triple SMT: some markets took the level, the rest held. Strongest evidence.",
         "Bullish: buy the market that held. Bearish: sell the market that held (it is the weak one).",
         "Read it at the New York open and in the Silver Bullet windows."],
  entry="Confirmation only.", stop="Per the model.", target="Per the model.",
  mist=["Using it away from a key level or killzone.", "Selecting the market that took liquidity instead of the one that held."],
  dash="SMT tab, 'Indices' market. Triple signals appear as banner cards above the pair signals.",
  chk=["NQ and ES sweep the previous high, YM does not. Which do you sell?", "YM, the weak market that failed to make the high."],
  d=dict(p=[50,56,62,68,76,84,78,70,62], z=[], l=[[76,"previous high"]], m=[[5,"NQ & ES take it","up"],[4,"YM fails","down"],[8,"sell weakest","down"]])),
 # ------------------------------------------------------------------ price action
 dict(id="pa_basics", g="Price action", t="Reading price action: levels and zones", tag="Price action",
  sum="The dashboard reads three things on every chart: structure, support/resistance zones and supply/demand zones.",
  why="Price remembers where it turned before. Marking those places gives you entries, stops and targets from the chart itself.",
  rules=["Structure: higher highs and lows is an uptrend; lower highs and lows a downtrend; mixed swings a range.",
         "Support/resistance zone: swing points clustered within 0.35 ATR. More touches mean a stronger zone. A broken resistance becomes support.",
         "Supply/demand zone: a tight base of 1-3 small candles, then an impulsive departure of at least 2 ATR.",
         "Fresh means untouched since; tested once is still usable; broken zones are dropped.",
         "EMA20 and EMA50 act as dynamic value in a trend."],
  entry="None here: see the five models.", stop="Beyond the zone.", target="The nearest opposing zone.",
  mist=["Drawing a line instead of a zone.", "Using a zone price has already closed through."],
  dash="Price action tab, 'Market read': trend, volatility (compressing or expanding), nearest support, resistance and fresh zones per timeframe.",
  chk=["A resistance zone breaks with a strong close. What is it now?", "Support (role flip)."],
  d=dict(p=[40,52,60,50,58,62,52,60,64,56,70,78,66,68,74], z=[[0,10,58,66,"resistance","sell"],[10,14,58,66,"now support","buy"]], m=[[10,"break","up"],[12,"retest","down"]])),
 dict(id="pa_zone", g="Price action", t="Zone reaction", tag="Price action · all timeframes",
  sum="A pin bar or engulfing candle at a fresh demand zone or a strong support level (mirror for supply).",
  why="At a place where price turned before, a rejection candle shows buyers defending it again, and the zone gives a tight stop.",
  rules=["Price trades into a fresh or once-tested demand zone, or a level with at least 2 touches.",
         "Pin bar: lower wick at least 60% of the range, small body, close in the upper part. Or a bullish engulfing with a body of at least 0.6 ATR.",
         "Candle closes above the zone's far edge.",
         "Target gives at least 1.5R."],
  entry="Buy stop just above the signal candle's high.", stop="Below the zone and the signal candle's low.", target="Nearest resistance or supply zone.",
  mist=["Pin bars in the middle of nowhere (no zone).", "Third and fourth touches of the same zone."],
  dash="Price action tab: 'Zone reaction'. The note names the zone, its touches and the trigger.",
  chk=["Why use a stop order above the candle, not a market order?", "It waits for price to confirm the rejection before you are in."],
  d=dict(p=[70,64,58,54,60,66,62,56,52,50,56,66], z=[[7,11,48,54,"demand (fresh)","buy"]], m=[[9,"pin bar","down"],[11,"stop order fills","up"]])),
 dict(id="pa_fakeout", g="Price action", t="False breakout", tag="Price action",
  sum="Price breaks a level, fails to hold, and reclaims it: the break was a trap.",
  why="Breakout traders buy the break and place stops back inside. When price returns, their exits fuel the reversal.",
  rules=["A level with at least 2 touches.",
         "Within 3 bars, price trades below it by wick or close (not more than 1.5 ATR beyond).",
         "A strong bullish candle then closes back above the whole zone.",
         "Target gives at least 1.5R."],
  entry="Buy stop above the reclaim candle.", stop="Below the false-break low.", target="Next resistance.",
  mist=["Buying the first wick back, before the close reclaims the zone.", "Ignoring a break that goes far beyond the level: that is a real break."],
  dash="Price action tab: 'False breakout'. The note says how far price went and how many touches the zone had.",
  chk=["A support breaks, then 3 bars later closes back above it. What do you do?", "Look for a bullish false-breakout entry with a stop below the break low."],
  d=dict(p=[64,58,54,56,52,40,44,56,66,74], z=[[0,5,50,56,"support","buy"]], m=[[5,"break","down"],[7,"reclaim","up"],[8,"stop order","down"]])),
 dict(id="pa_retest", g="Price action", t="Break and retest", tag="Price action",
  sum="After a strong close through a level, price comes back to the flipped zone, holds, and continues.",
  why="The old resistance becomes support. Traders who missed the break buy the retest, and trapped sellers cover.",
  rules=["A level with at least 2 touches, broken by a displacement candle (body of at least 0.7 ATR) within the last 15 bars.",
         "No candle closes back below the zone.",
         "Price touches the zone again, within the last 3 bars.",
         "A bullish candle closes above the previous high and the zone."],
  entry="Buy stop above the rejection candle.", stop="Below the flipped zone.", target="Next level or supply zone.",
  mist=["Entering on the break itself.", "A retest that closes back through the zone."],
  dash="Price action tab: 'Break & retest'. The note gives the break time and the retest.",
  chk=["What happens to a broken resistance zone?", "It flips to support, and the retest is the entry area."],
  d=dict(p=[52,60,56,62,58,70,78,72,66,62,70,80], z=[[0,5,58,64,"resistance","sell"],[5,11,58,64,"flipped support","buy"]], m=[[5,"displacement","up"],[9,"retest holds","down"],[11,"TP","up"]])),
 dict(id="pa_pullback", g="Price action", t="With-trend pullback", tag="Price action",
  sum="In a trend, buy the dip to the EMA20 once a trigger candle shows it is holding.",
  why="Trends advance in waves. The pullback to the moving average is the lowest-risk point to join them.",
  rules=["Uptrend: EMA20 above EMA50, EMA20 rising, and higher lows.",
         "Pullback low reaches the EMA20 and holds above the EMA50 (within 0.5 ATR).",
         "The pullback has real depth (at least 1.5 ATR from the recent high).",
         "Trigger: pin bar, engulfing, or a bullish close above the prior high that closes above the EMA20."],
  entry="Buy stop above the trigger.", stop="Below the pullback low.", target="Prior high or next resistance.",
  mist=["Buying a pullback in a flattening trend.", "No trigger: a touch of the EMA is not a signal."],
  dash="Price action tab: 'With-trend pullback'. A score point is added when the trend is intact.",
  chk=["Which condition cancels a with-trend long?", "A lower low (the higher-lows rule fails) or EMA20 dropping below EMA50."],
  d=dict(p=[30,40,36,48,44,56,52,64,60,72], z=[], l=[], m=[[4,"pullback to EMA20","down"],[5,"trigger","up"],[9,"TP","up"]])),
 dict(id="pa_inside", g="Price action", t="Inside-bar breakout", tag="Price action",
  sum="A quiet bar inside a big one, at value, then a break in the trend's direction.",
  why="The inside bar shows compression and a pause. The break shows which side wins, and the mother bar defines a small, clear stop.",
  rules=["Mother bar at least 1 ATR tall; the next bar sits entirely inside it.",
         "Location: at the EMA20 or at a level with at least 2 touches.",
         "Trend up (EMA20 above EMA50, rising).",
         "Breakout bar closes above the mother's high with a body of at least 0.4 ATR."],
  entry="Buy stop above the breakout bar.", stop="Below the mother bar's low.", target="Next resistance, at least 1.5R.",
  mist=["Trading inside bars in the middle of a range.", "A mother bar that is tiny."],
  dash="Price action tab: 'Inside-bar breakout'.",
  chk=["Why does the mother bar's size matter?", "A tiny mother bar gives a tiny stop but also no meaningful compression."],
  d=dict(p=[40,46,44,58,50,54,66,72], z=[[3,5,44,60,"mother bar range","neu"]], m=[[4,"inside bar","down"],[6,"breakout","up"]])),
]

GROUPS = ["Foundations", "Workflow", "ICT models", "SMC models", "Confirmation", "Price action"]

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strategy Academy</title>
<style>
:root{--bg:#fbfcfd;--card:#fff;--ink:#16202b;--mute:#5d6b7a;--line:#dde3ea;--rail:#0f1a24;--railink:#c4cfdb;--accent:#2a62d9;--buy:#0d8a4f;--buyb:rgba(13,138,79,.14);--sell:#cc2f26;--sellb:rgba(204,47,38,.13);--neub:rgba(90,107,126,.14)}
@media (prefers-color-scheme:dark){:root{--bg:#0e1319;--card:#151b23;--ink:#e6ebf1;--mute:#93a0af;--line:#27303b;--accent:#7aa6ff;--buy:#4cd18a;--sell:#ff7b72}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 "Iowan Old Style",Palatino,"Palatino Linotype",Georgia,serif}
.sans,nav,h1,h2,h3,button,.tag,.chip{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.shell{display:grid;grid-template-columns:270px 1fr;min-height:100vh}
nav{background:var(--rail);color:var(--railink);padding:18px 12px;position:sticky;top:0;height:100vh;overflow:auto;font-size:14px}
nav .brand{color:#fff;font-weight:700;font-size:16px;padding:4px 10px 2px}nav .back{display:block;padding:2px 10px 12px;color:#8fb0e8;text-decoration:none;font-size:13px}
nav h4{margin:16px 10px 4px;font-size:12px;color:#7d8d9f;font-weight:600}
nav a.l{display:flex;gap:8px;align-items:center;padding:6px 10px;border-radius:8px;color:inherit;text-decoration:none;cursor:pointer}
nav a.l:hover{background:#18273a}nav a.l.on{background:#243a56;color:#fff}nav .ck{width:14px;color:#4cd18a;font-size:12px}
.prog{margin:8px 10px 0;height:6px;border-radius:3px;background:#22313f;overflow:hidden}.prog i{display:block;height:100%;background:#4cd18a;width:0}
main{max-width:820px;padding:34px 30px 80px}
.tag{display:inline-block;font-size:13px;color:var(--mute);margin-bottom:4px}
h1{font-size:34px;line-height:1.15;margin:0 0 10px;letter-spacing:-.01em}h2{font-size:15px;margin:26px 0 8px;color:var(--mute);font-weight:600}
p.lead{font-size:20px;line-height:1.5;margin:6px 0 14px}
.fig{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:6px;margin:16px 0}svg{width:100%;height:auto;display:block}
ul{padding-left:22px;margin:6px 0}li{margin:5px 0}
.how{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:8px 0}
.how div{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--card);font-size:15px}
.how b{display:block;font-family:ui-sans-serif,system-ui,sans-serif;font-size:13px;color:var(--mute);margin-bottom:2px}
.dash{border-left:3px solid var(--accent);padding:2px 0 2px 14px;margin:6px 0}
.chk{border:1px dashed var(--line);border-radius:10px;padding:12px 14px;margin:20px 0}.chk button{margin-top:6px}
button{font:600 14px ui-sans-serif,system-ui,sans-serif;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;background:var(--accent);color:#fff}
button.sec{background:var(--neub);color:var(--ink)}.ans{display:none;margin-top:8px}.ans.on{display:block}
.pager{display:flex;justify-content:space-between;gap:10px;margin-top:28px;flex-wrap:wrap}
.start .path{display:grid;gap:8px;margin:14px 0}.start .path a{display:block;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:inherit;text-decoration:none}
.start .path a small{color:var(--mute);font-family:ui-sans-serif,system-ui,sans-serif}
.t{font-family:ui-sans-serif,system-ui,sans-serif;font-size:11px;fill:var(--ink)}.tm{fill:var(--mute)}
@media(max-width:860px){.shell{grid-template-columns:1fr}nav{position:static;height:auto;max-height:240px}main{padding:22px 16px 60px}.how{grid-template-columns:1fr}h1{font-size:27px}}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style></head><body>
<div class="shell"><nav id="toc"></nav><main id="main"></main></div>
<script>
const D=__DATA__,G=__GROUPS__,$=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let done={};try{done=JSON.parse(localStorage.getItem("acad")||"{}")}catch(e){}
const save=()=>{try{localStorage.setItem("acad",JSON.stringify(done))}catch(e){}};
function svg(d){const W=560,H=210,P=26,n=d.p.length,X=i=>P+i*(W-2*P)/(n-1),Y=v=>H-24-v*1.6,col={buy:"var(--buy)",sell:"var(--sell)",neu:"var(--mute)"};
 let o=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Schematic price path">`;
 (d.z||[]).forEach(z=>{const c=col[z[5]]||col.neu;o+=`<rect x="${X(z[0])}" y="${Y(z[3])}" width="${X(z[1])-X(z[0])}" height="${Y(z[2])-Y(z[3])}" fill="${c}" opacity=".16"/>`+(z[4]?`<text class="t" x="${X(z[0])+4}" y="${Y(z[3])+12}" fill="${c}">${esc(z[4])}</text>`:"")});
 (d.l||[]).forEach(l=>{o+=`<line x1="${P}" x2="${W-P}" y1="${Y(l[0])}" y2="${Y(l[0])}" stroke="var(--mute)" stroke-dasharray="4 4"/><text class="t tm" x="${W-P}" y="${Y(l[0])-4}" text-anchor="end">${esc(l[1])}</text>`});
 o+=`<polyline fill="none" stroke="var(--ink)" stroke-width="2" stroke-linejoin="round" points="${d.p.map((v,i)=>X(i)+","+Y(v)).join(" ")}"/>`;
 (d.m||[]).forEach(m=>{const x=X(m[0]),y=Y(d.p[m[0]]),up=m[2]==="up",ty=up?y-12:y+20;o+=`<circle cx="${x}" cy="${y}" r="4" fill="var(--accent)"/><text class="t" x="${Math.min(Math.max(x,70),W-70)}" y="${ty}" text-anchor="middle">${esc(m[1])}</text>`});
 return o+"</svg>"}
const list=(a,tag="ul")=>`<${tag}>${a.map(x=>`<li>${esc(x)}</li>`).join("")}</${tag}>`;
function toc(cur){const pct=Math.round(100*Object.keys(done).length/D.length);
 $("#toc").innerHTML=`<div class="brand">Strategy Academy</div><a class="back" href="/">Back to the dashboard</a><a class="l ${cur==="start"?"on":""}" href="#start">Start here</a>`+
 G.map(g=>`<h4>${esc(g)}</h4>`+D.filter(l=>l.g===g).map(l=>`<a class="l ${l.id===cur?"on":""}" href="#${l.id}"><span class="ck">${done[l.id]?"&#10003;":""}</span>${esc(l.t)}</a>`).join("")).join("")+
 `<div class="prog" title="${pct}% learned"><i style="width:${pct}%"></i></div><div style="margin:6px 10px;font-size:12px;color:#7d8d9f">${Object.keys(done).length} of ${D.length} lessons marked learned</div>`}
function start(){toc("start");$("#main").innerHTML=`<div class="start"><span class="tag">Strategy Academy</span><h1>Learn every model the dashboard trades</h1>
 <p class="lead">Each lesson shows the idea, the exact rules, where to enter, where to put the stop, the mistakes that cost money, and what the matching dashboard card means.</p>
 <h2>Suggested path</h2><div class="path">${G.map(g=>{const f=D.find(l=>l.g===g);return `<a href="#${f.id}"><b>${esc(g)}</b><br><small>${D.filter(l=>l.g===g).map(l=>esc(l.t)).join(" - ")}</small></a>`}).join("")}</div>
 <h2>How to practise</h2>${list(["Read a lesson, then open the matching tab on the dashboard with demo data.","Find a card and say aloud which rule each line of its note satisfies.","Demo-trade for at least 50 setups per model before using real money.","Educational content, not financial advice."])}</div>`;scrollTo(0,0)}
function lesson(id){const i=D.findIndex(l=>l.id===id);if(i<0)return start();const l=D[i];toc(id);
 $("#main").innerHTML=`<span class="tag">${esc(l.g)} &middot; ${esc(l.tag)}</span><h1>${esc(l.t)}</h1><p class="lead">${esc(l.sum)}</p>
 <div class="fig">${svg(l.d)}</div><h2>Why it works</h2><p>${esc(l.why)}</p><h2>The rules</h2>${list(l.rules)}
 <h2>Trade plan</h2><div class="how"><div><b>Entry</b>${esc(l.entry)}</div><div><b>Stop</b>${esc(l.stop)}</div><div><b>Target</b>${esc(l.target)}</div></div>
 <h2>Common mistakes</h2>${list(l.mist)}<h2>On the dashboard</h2><p class="dash">${esc(l.dash)}</p>
 <div class="chk"><b class="sans">Quick check</b><p style="margin:6px 0">${esc(l.chk[0])}</p><button class="sec" id="rv">Show the answer</button><div class="ans" id="an"><b>${esc(l.chk[1])}</b></div></div>
 <button id="mk" class="${done[id]?"sec":""}">${done[id]?"Marked as learned":"Mark as learned"}</button>
 <div class="pager">${i>0?`<button class="sec" onclick="location.hash='${D[i-1].id}'">Previous: ${esc(D[i-1].t)}</button>`:"<span></span>"}${i<D.length-1?`<button onclick="location.hash='${D[i+1].id}'">Next: ${esc(D[i+1].t)}</button>`:""}</div>`;
 $("#rv").onclick=()=>$("#an").classList.toggle("on");$("#mk").onclick=()=>{done[id]?delete done[id]:done[id]=1;save();lesson(id)};document.title=l.t+" - Strategy Academy";scrollTo(0,0)}
function route(){const h=location.hash.slice(1);h&&h!=="start"?lesson(h):start()}
addEventListener("hashchange",route);route();
</script></body></html>"""


def render():
    return PAGE.replace("__DATA__", json.dumps(LESSONS)).replace("__GROUPS__", json.dumps(GROUPS))


PAGE_HTML = render()

if __name__ == "__main__":
    with open("ict_learn.html", "w", encoding="utf-8") as f:
        f.write(PAGE_HTML)
    print(f"Wrote ict_learn.html ({len(LESSONS)} lessons)")
