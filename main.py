import os, time, logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests, pandas as pd, numpy as np

BOT_TOKEN=os.getenv('BOT_TOKEN'); CHAT_ID=os.getenv('CHAT_ID'); API_KEY=os.getenv('API_KEY')
SYMBOL='XAU/USD'; INTERVAL='5min'; TUX_LENGTH=12
EMA_FAST=9; EMA_SLOW=21; RSI_PERIOD=14; ATR_PERIOD=14; ADX_PERIOD=14
MIN_SIGNAL_SCORE=65; MIN_SCORE_MARGIN=6; MAX_CANDLE_AGE_MINUTES=8
TP1_ATR=1.0; TP2_ATR=2.0; TP3_ATR=3.0; SL_ATR=1.5
TEHRAN=ZoneInfo('Asia/Tehran'); UTC=timezone.utc
logging.basicConfig(level=logging.INFO,format='%(asctime)s | %(levelname)s | %(message)s')
log=logging.getLogger('gold_bot'); last_analyzed=None; last_signal=None

def now_utc(): return datetime.now(UTC)
def now_tehran(): return datetime.now(TEHRAN)
def in_window(): return 10 <= now_tehran().hour < 19
def fmt(x): return f'{float(x):.2f}'

def telegram(text):
    try:
        r=requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',data={'chat_id':CHAT_ID,'text':text,'disable_web_page_preview':True},timeout=20)
        if r.status_code==200: return True
        log.error('Telegram error %s: %s',r.status_code,r.text[:300])
    except requests.RequestException as e: log.error('Telegram connection error: %s',e)
    return False

def get_data():
    try:
        r=requests.get('https://api.twelvedata.com/time_series',params={'symbol':SYMBOL,'interval':INTERVAL,'outputsize':100,'apikey':API_KEY,'timezone':'UTC','format':'JSON'},timeout=20)
        if r.status_code==429: log.warning('Twelve Data rate limit (429); waiting for next candle.'); return None
        r.raise_for_status(); p=r.json()
        if 'values' not in p: log.error('Twelve Data response: %s',p); return None
        df=pd.DataFrame(p['values']); df['datetime']=pd.to_datetime(df['datetime'],utc=True,errors='coerce')
        for c in ['open','high','low','close']: df[c]=pd.to_numeric(df[c],errors='coerce')
        return df.dropna().sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)
    except Exception as e: log.error('Market data error: %s',e); return None

def closed_data(df):
    now=now_utc(); boundary=now.replace(minute=(now.minute//5)*5,second=0,microsecond=0); target=boundary-timedelta(minutes=5)
    x=df[df.datetime<=target]
    if x.empty: return None
    age=(now-x.iloc[-1].datetime.to_pydatetime()).total_seconds()/60
    return None if age>MAX_CANDLE_AGE_MINUTES else x

def rsi(s,n=14):
    d=s.diff(); g=d.clip(lower=0); l=-d.clip(upper=0); ag=g.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); al=l.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); z=100-100/(1+ag/al.replace(0,np.nan)); return z.mask((al==0)&(ag>0),100).mask((ag==0)&(al>0),0)

def atr(df,n=14):
    pc=df.close.shift(); tr=pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1); return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def adx_calc(df,n=14):
    up=df.high.diff(); down=-df.low.diff(); plus=pd.Series(np.where((up>down)&(up>0),up,0.),index=df.index); minus=pd.Series(np.where((down>up)&(down>0),down,0.),index=df.index); pc=df.close.shift(); tr=pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1); a=tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); p=100*plus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a.replace(0,np.nan); m=100*minus.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a.replace(0,np.nan); dx=100*(p-m).abs()/(p+m).replace(0,np.nan); return dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean(),p,m

def indicators(df):
    df=df.copy(); df['hlc3']=(df.high+df.low+df.close)/3; df['tux']=df.hlc3.ewm(span=TUX_LENGTH,adjust=False).mean(); df['ema9']=df.close.ewm(span=EMA_FAST,adjust=False).mean(); df['ema21']=df.close.ewm(span=EMA_SLOW,adjust=False).mean(); df['rsi']=rsi(df.close,RSI_PERIOD); df['atr']=atr(df,ATR_PERIOD); df['adx'],df['pdi'],df['mdi']=adx_calc(df,ADX_PERIOD); return df

def tux_state(df):
    p,c=df.iloc[-2],df.iloc[-1]
    if p.hlc3<=p.tux and c.hlc3>c.tux: return 'BUY'
    if p.hlc3>=p.tux and c.hlc3<c.tux: return 'SELL'
    if c.hlc3>c.tux: return 'BUY_BIAS'
    if c.hlc3<c.tux: return 'SELL_BIAS'
    return 'NEUTRAL'

def wick_ok(c,side):
    body=abs(float(c.close)-float(c.open))
    if body<0.01: return True
    upper=float(c.high)-max(float(c.open),float(c.close)); lower=min(float(c.open),float(c.close))-float(c.low)
    return upper<=1.5*body if side=='BUY' else lower<=1.5*body

def make_signal(df):
    df=df.dropna().copy()
    if len(df)<30: return None
    c=df.iloc[-1]; state=tux_state(df); buy=sell=0
    if state=='BUY': buy+=45
    elif state=='BUY_BIAS': buy+=32
    if state=='SELL': sell+=45
    elif state=='SELL_BIAS': sell+=32
    if c.ema9>c.ema21: buy+=10
    elif c.ema9<c.ema21: sell+=10
    if c.pdi>c.mdi: buy+=10
    elif c.mdi>c.pdi: sell+=10
    if 52<=c.rsi<=68: buy+=8
    elif 32<=c.rsi<=48: sell+=8
    if c.adx>=25:
        if c.pdi>c.mdi: buy+=12
        elif c.mdi>c.pdi: sell+=12
    elif c.adx>=20:
        if c.pdi>c.mdi: buy+=6
        elif c.mdi>c.pdi: sell+=6
    if c.close>c.open: buy+=5
    elif c.close<c.open: sell+=5
    if buy>=MIN_SIGNAL_SCORE and buy-sell>=MIN_SCORE_MARGIN: side='BUY'; score=buy
    elif sell>=MIN_SIGNAL_SCORE and sell-buy>=MIN_SCORE_MARGIN: side='SELL'; score=sell
    else: return None
    if not wick_ok(c,side): return None
    return {'side':side,'score':score,'state':state,'price':float(c.close),'atr':float(c.atr),'rsi':float(c.rsi),'adx':float(c.adx),'ema9':float(c.ema9),'ema21':float(c.ema21),'candle':c.datetime}

def message(s):
    e,a=s['price'],s['atr']
    if s['side']=='BUY': tp1,tp2,tp3,sl=e+a,e+2*a,e+3*a,e-1.5*a; title='🟢 سیگنال خرید طلا'; tux='خرید'
    else: tp1,tp2,tp3,sl=e-a,e-2*a,e-3*a,e+1.5*a; title='🔴 سیگنال فروش طلا'; tux='فروش'
    return f'''{title}\n━━━━━━━━━━━━━━\n📌 نماد: {SYMBOL}\n⏱ تایم‌فریم: ۵ دقیقه\n💰 قیمت ورود: {fmt(e)}\n\n🎯 هدف اول (TP1): {fmt(tp1)}\n🎯 هدف دوم (TP2): {fmt(tp2)}\n🎯 هدف سوم (TP3): {fmt(tp3)}\n🛑 حد ضرر (SL): {fmt(sl)}\n\n📊 فیلتر اصلی: TUX EMA Scalper\n🔹 وضعیت TUX: {tux}\n📈 EMA 9: {fmt(s['ema9'])}\n📉 EMA 21: {fmt(s['ema21'])}\n📊 RSI: {s['rsi']:.1f}\n📊 ADX: {s['adx']:.1f}\n📐 ATR: {fmt(a)}\n⭐ قدرت سیگنال: {s['score']}/100\n\n⚙️ معاملات خودکار: خاموش'''

def analyze():
    global last_analyzed,last_signal
    df=get_data(); x=closed_data(df) if df is not None else None
    if x is None: return
    candle=x.iloc[-1].datetime
    if candle==last_analyzed: return
    last_analyzed=candle; s=make_signal(indicators(x))
    if not s or not in_window(): return
    key=(candle,s['side'])
    if key==last_signal: return
    last_signal=key; telegram(message(s)); log.info('VALID %s | score=%d | price=%.2f | TUX=%s',s['side'],s['score'],s['price'],s['state'])

def sleep_next():
    now=now_utc(); b=now.replace(minute=(now.minute//5)*5,second=0,microsecond=0)+timedelta(minutes=5,seconds=75); time.sleep(max(10,(b-now).total_seconds()))

def main():
    if not all([BOT_TOKEN,CHAT_ID,API_KEY]): raise RuntimeError('Set BOT_TOKEN, CHAT_ID and API_KEY in Railway Variables.')
    log.info('FINAL GOLD BOT | XAU/USD | 5min | TUX EMA Scalper Length=12 HLC3 | Auto Trading OFF')
    while True:
        try:
            if in_window(): analyze(); sleep_next()
            else: time.sleep(60)
        except Exception as e: log.exception('Main loop error: %s',e); time.sleep(30)

if __name__=='__main__': main()
