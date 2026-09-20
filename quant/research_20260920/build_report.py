"""Create a Vietnamese quant research PDF and matching Markdown from audited outputs."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
from datetime import datetime, timezone
from xml.sax.saxutils import escape

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/quant/research-20260920'
sys.path.insert(0,str(OUT/'python-deps'))
os.environ['MPLCONFIGDIR']=str(OUT/'matplotlib-cache')
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak

NAVY='#183247'; TEAL='#167D8D'; RED='#B74D52'; GOLD='#C38F34'; GRAY='#52616B'
plt.rcParams.update({'font.family':'DejaVu Sans','axes.spines.top':False,'axes.spines.right':False,
                     'font.size':9,'axes.labelcolor':GRAY,'xtick.color':GRAY,'ytick.color':GRAY,
                     'axes.titleweight':'bold','axes.titlecolor':NAVY,'figure.facecolor':'white'})
replay=json.loads((OUT/'replay-results.json').read_text(encoding='utf-8'))
stats=json.loads((OUT/'existing-statistics.json').read_text(encoding='utf-8'))
manifest=json.loads((OUT/'data-manifest.json').read_text(encoding='utf-8'))
live=json.loads((ROOT/'data/reviews/status-2026-09-20/v2-report.json').read_text(encoding='utf-8-sig'))
universe=json.loads((ROOT/'data/reviews/status-2026-09-20/universe-report.json').read_text(encoding='utf-8-sig'))
audit=json.loads((ROOT/'data/reviews/status-2026-09-20/audit.json').read_text(encoding='utf-8-sig'))
VARIANTS=['baseline','batch_exit','peak_guard','batch_peak']
U=['two','five','five_cap10']
UL={'two':'2 coin / cap $20','five':'5 coin / cap $20','five_cap10':'5 coin / cap $10'}
VL={'baseline':'Gốc','batch_exit':'Bán theo lô','peak_guard':'Chặn từ đỉnh','batch_peak':'Lô + từ đỉnh'}

def run(w,u,v,b=5):
    return next(r for r in replay['runs'] if r['window']==w and r['summary']['universe']==u and r['summary']['variant']==v and r['summary']['bps']==b)
def s(w,u,v,b=5): return run(w,u,v,b)['summary']
def blocks(u,v): return [s(f'block{i}',u,v) for i in range(1,9)]
def f(x,n=2): return f'{x:,.{n}f}'
def pct(x): return f'{x:+.2f}%'
def old(strategy,period='holdout',dataset='history',bps=5):
    return next(r for r in stats['runs'] if r['dataset']==dataset and r['periodOriginalLabel']==period and r['strategy']==strategy and r['slippageBpsPerSide']==bps)
def savefig(name):
    path=OUT/name; plt.savefig(path,dpi=190,bbox_inches='tight',facecolor='white'); plt.close(); return path

# Primary research figures, generated directly from the audited result matrices.
fig,ax=plt.subplots(figsize=(10.2,5.5))
matrix=np.array([[s(f'block{i}',u,v)['returnPct'] for i in range(1,9)] for u in U for v in VARIANTS])
im=ax.imshow(matrix,cmap='RdYlGn',vmin=-13,vmax=13,aspect='auto')
ax.set_yticks(range(12),[f'{u} | {v}' for u in U for v in VARIANTS],fontsize=8)
labels=['Sep-Nov24','Dec24-Feb25','Mar-May25','Jun-Aug25','Sep-Nov25','Dec25-Feb26','Mar-May26','Jun-Aug26']
ax.set_xticks(range(8),labels,rotation=25,ha='right',fontsize=8)
for (y,x),val in np.ndenumerate(matrix): ax.text(x,y,f'{val:+.2f}',ha='center',va='center',fontsize=8)
for y in [3.5,7.5]: ax.axhline(y,color='white',lw=3)
ax.set_title('Fresh $50 starts: net return (%) by non-overlapping 3-month block',pad=14)
fig.colorbar(im,ax=ax,fraction=.025,pad=.02,label='Portfolio return (%)')
fig.tight_layout(); savefig('startup-heatmap.png')

fig,(ax,ddax)=plt.subplots(2,1,figsize=(10,5.6),sharex=True,gridspec_kw={'height_ratios':[2,1]})
for v,color in [('baseline',RED),('batch_exit',GOLD),('peak_guard',TEAL),('batch_peak',NAVY)]:
    r=run('continuous','five',v)
    curve=r['equityCurve']; x=[datetime.fromisoformat(p['time'].replace('Z','+00:00')) for p in curve]
    equity=np.array([p['equity'] for p in curve]); peak=np.maximum.accumulate(np.r_[50,equity])[1:]
    ax.plot(x,equity,label=v,color=color,lw=1.3)
    ddax.plot(x,(equity/peak-1)*100,color=color,lw=1)
ax.axhline(50,color=GRAY,lw=.7,ls=':'); ax.set_ylabel('Marked equity ($)')
ax.set_title('Five coins, $20 cap: early peak guard can lock in one historical path')
ax.legend(ncol=2,fontsize=8); ddax.set_ylabel('Close-sampled DD (%)')
ddax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y')); ddax.grid(alpha=.15)
fig.tight_layout(); savefig('five-equity-and-dd.png')

fig,axs=plt.subplots(1,3,figsize=(10,3.1),sharey=True)
for ax,u in zip(axs,U):
    for v,color in [('baseline',RED),('batch_exit',GOLD),('peak_guard',TEAL),('batch_peak',NAVY)]:
        ax.plot([0,5,10],[s('continuous',u,v,b)['returnPct'] for b in [0,5,10]],marker='o',ms=4,lw=1.3,label=v,color=color)
    ax.axhline(0,color=GRAY,lw=.8,ls=':'); ax.set_title(u,fontsize=10); ax.set_xlabel('Adverse cost (bps / side)'); ax.set_xticks([0,5,10]); ax.grid(alpha=.15)
axs[0].set_ylabel('Net portfolio return (%)'); axs[2].legend(fontsize=7,loc='lower right')
fig.suptitle('Full path replay: costs change fills, capacity and stop dates',color=NAVY,fontweight='bold',y=1.02)
fig.tight_layout(); savefig('cost-sensitivity.png')

raw={p.name.split('-')[0]:json.loads(p.read_text(encoding='utf-8'))['rows'] for p in (OUT/'candles').glob('*.json')}
symbols=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT']
hourly=np.array([np.diff(np.log([float(r[4]) for r in raw[sym][100:]])) for sym in symbols])
corr=np.corrcoef(hourly)
fig,ax=plt.subplots(figsize=(5.4,3.5)); im=ax.imshow(corr,vmin=0,vmax=1,cmap='Blues')
ax.set_xticks(range(5),[x[:-4] for x in symbols]); ax.set_yticks(range(5),[x[:-4] for x in symbols])
for (y,x),val in np.ndenumerate(corr): ax.text(x,y,f'{val:.2f}',ha='center',va='center',color='white' if val>.72 else NAVY)
ax.set_title('Observed hourly log-return correlation\nSep 2024 - Sep 19, 2026',fontsize=11,pad=10)
fig.colorbar(im,ax=ax,fraction=.045,pad=.04); fig.tight_layout(); savefig('asset-correlation.png')

pages=[]
def page(title,kicker='RESEARCH NOTE  |  Q-20260920'):
    p={'title':title,'kicker':kicker,'items':[]}; pages.append(p); return p['items']
def para(p,text): p.append(('p',text))
def table(p,headers,rows,widths=None): p.append(('table',(headers,rows,widths)))
def figure(p,name,caption,height): p.append(('figure',(OUT/name,caption,height)))
def small(p,text): p.append(('small',text))

p=page('Nghiên cứu quant\nBTC/ETH và danh mục 5 coin','20 / 09 / 2026  |  NGHIÊN CỨU CÓ THỂ TÁI LẬP')
para(p,'<b>Kết luận: chưa chứng minh được lợi thế sinh lời bền vững sau chi phí.</b> Bot vận hành ổn và V2 đang lãi nhẹ, nhưng bằng chứng forward còn quá ít. Backtest nhiều mốc khởi đầu không xác nhận một phương án thay thế ổn định.')
table(p,['Dữ liệu','Kiểm định mới','Đối chiếu cũ'],[['90.380 nến giờ\n5 coin / hơn 2 năm','540 kịch bản mô phỏng\n12 cấu hình chủ động','84 lần chạy cũ\n15.555 fills khớp sổ']],None)
para(p,'<b>Phát hiện chính</b>')
para(p,'1. Cả 12 cấu hình chủ động đều có <b>trung vị lợi nhuận âm</b> trên tám cửa sổ ba tháng khởi đầu với $50. Bản gốc 2 coin chỉ lãi ở 3/8 cửa sổ.')
para(p,'2. Một cấu hình 5 coin có chặn sụt giảm từ đỉnh đạt <b>+11,76%</b> trên đường chạy hai năm, nhưng ngừng mua khoảng <b>78,6%</b> thời gian và chỉ lãi ở <b>2/8</b> mốc khởi đầu. Đây là kết quả phụ thuộc đường đi, chưa phải lợi thế đã kiểm chứng.')
para(p,'3. Bán phần đang chờ thoát trong cùng giờ đưa hàng chờ về 0 trên đường liên tục base-case, nhưng <b>không cải thiện lợi nhuận hoặc drawdown một cách nhất quán</b>. Một số kịch bản còn lượng dư sát ngưỡng làm tròn.')
para(p,'4. Breakout từng có kết quả dương trong sáu tháng vẫn có khoảng bất định bao gồm 0; lợi nhuận giảm mạnh khi tăng chi phí thực thi. Không đủ cơ sở chọn nó làm chiến lược thắng.')
para(p,'<b>Quyết định nghiên cứu:</b> tiếp tục paper, giữ đối chứng hiện tại, ưu tiên đo và kiểm định cơ chế thoát. Chưa đạt tiêu chí chuyển sang vốn thật hoặc tăng rủi ro dựa trên báo cáo này.')
small(p,'Phạm vi: spot long-only, vốn mô phỏng $50, không đòn bẩy. Mốc dữ liệu lịch sử cuối: 20/09/2026 00:00 UTC, loại trừ thời điểm cuối. Snapshot forward: 20/09/2026 khoảng 13:08 UTC+7. Tất cả lịch sử trong báo cáo là dữ liệu phát triển đã được xem; không gọi là OOS chưa chạm.')

p=page('01  Câu hỏi và thiết kế nghiên cứu')
para(p,'Nghiên cứu kiểm tra <b>lợi thế kinh tế</b> và <b>hiệu quả kiểm soát rủi ro</b> riêng biệt. Một cơ chế giảm lỗ bằng cách dừng giao dịch có thể hữu ích về rủi ro, nhưng không chứng minh khả năng tạo lợi nhuận kỳ vọng dương.')
table(p,['Giả thuyết','Can thiệp cố định','Tiêu chí quan sát'],[
['H0: chưa có edge dương','Giữ nguyên tín hiệu, phí và giới hạn','Lãi ròng, độ ổn định theo mốc bắt đầu, benchmark'],
['H1: thoát theo lô tốt hơn?','Bán hết phần chờ trong cùng giờ; mỗi fill <= $5','Hàng chờ, drawdown, lợi nhuận và turnover'],
['H2: bảo vệ từ đỉnh hữu ích?','Chặn vĩnh viễn khi equity <= 94% đỉnh quan sát','Mức sụt giảm, thời gian bị chặn, cơ hội bỏ lỡ'],
['H3: thêm coin tạo lợi ích?','2 coin vs 5 coin; thêm nhánh 5 coin cap $10','Tách tác động cơ hội và mức vốn đầu tư']], [105,182,205])
para(p,'Thiết kế 2x2 cho cơ chế thoát: <b>baseline</b>, <b>batch_exit</b>, <b>peak_guard</b>, <b>batch_peak</b>. Ba universe/cap, ba mức chi phí và 10 cửa sổ tạo 360 kịch bản chủ động. Thêm cash và passive10 tạo tổng cộng 540 lần chạy; các lần chạy trùng benchmark không được coi là bằng chứng độc lập.')
table(p,['Cửa sổ','Mục đích'],[
['01/09/2024 - 01/09/2026','Một tài khoản liên tục; bộc lộ tác động tích lũy và ngừng giao dịch.'],
['8 cửa sổ 3 tháng, không chồng lấn','Mỗi cửa sổ khởi đầu $50 và không vị thế; đo độ nhạy theo ngày bắt đầu.'],
['01/09/2026 - 20/09/2026','19 ngày gần đây; chẩn đoán ngắn hạn, không đủ chứng minh độ bền.']], [170,322])
para(p,'Các cửa sổ ba tháng là <b>kịch bản khởi đầu mới</b>, không phải chiến lược tái cấp vốn theo quý. Không nối chúng thành một đường lãi kép. Đây cũng không phải walk-forward huấn luyện mô hình: không có bước fit/optimize trên quá khứ.')
small(p,'Protocol và ngưỡng 6% được ghi trước khi xem kết quả ứng viên mới. Có làm rõ cách kiểm tra ngưỡng ở giá mở cửa trước khi phân tích đầu ra; không thay ngưỡng sau khi xem kết quả. Dữ liệu cũ đã được xem và universe được chọn ở hiện tại, nên nghiên cứu vẫn mang tính khám phá.')

p=page('02  Dữ liệu, nguồn và tính nhân quả')
table(p,['Kiểm tra dữ liệu','Kết quả'],[
['Nguồn','Binance public spot /api/v3/klines; không cần API key'],
['Universe','BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT'],
['Độ phủ','18.076 nến/coin: 100 giờ warm-up + 17.976 giờ đánh giá'],
['Tính toàn vẹn','Không gap, không timestamp trùng, OHLCV hợp lệ, không nến chưa đóng'],
['Đối chiếu lấy lại','15 đoạn x 24 giờ lấy lại độc lập theo request; tất cả khớp toàn bộ trường'],
['Bảo toàn chứng cứ','SHA-256 của cache, source, protocol, request và response; cache cũ không ghi đè']], [160,332])
para(p,'<b>Luồng thông tin mỗi giờ t:</b> chỉ báo tính từ các nến đã đóng đến t-1; giá mở cửa t làm đại diện cho quote; runner/policy.py quyết định; khớp với chi phí bất lợi; cuối giờ t mới định giá theo close. High/low/close của giờ đang giao dịch không được dùng để quyết định lệnh tại open.')
para(p,'Luật BUY: quote > SMA20 > SMA50 và lợi nhuận 24 giờ của nến đóng > 0. Luật SELL: quote < SMA20 và lợi nhuận 24 giờ < 0, hoặc vi phạm ngân sách lỗ; phần thoát còn lại tiếp tục chờ. Ưu tiên BTC, ETH, SOL, BNB, XRP.')
para(p,'Replay mới gọi trực tiếp <b>policy đang chạy</b>, giảm sai lệch do viết lại luật. Tuy vậy, open mỗi giờ chỉ gần đúng quote lúc phút 02; không tái hiện historical spread, latency hay quyết định AI. Các kết quả cũ sử dụng prior close để so với SMA, nên không được trộn như cùng một backtest.')
para(p,'<b>Giới hạn dữ liệu:</b> lấy lại từ cùng Binance xác nhận tính nhất quán, không phải xác minh chéo sàn. Chọn năm coin ở hiện tại còn rủi ro selection/survivorship bias. Dữ liệu không chứa độ sâu sổ lệnh, lot-size lịch sử, depeg USDT hay trình tự biến động trong giờ.')
small(p,'Phần 17-19/09 được bổ sung nhưng trạng thái forward đã được xem trước đó; không được gọi là tập kiểm định hoàn toàn chưa biết. Tài liệu nguồn [1]; manifest đầy đủ trong data/quant/research-20260920/data-manifest.json.')

p=page('03  Bằng chứng forward hiện tại')
rows=[]
for key,label,rep,exp in [('ai','V2 + AI',live,'v2'),('control','V2 theo luật',live,'v2'),('two','Universe 2 coin',universe,'universe'),('five','Universe 5 coin',universe,'universe')]:
    a=rep['accounts'][key]; l=audit['experiments'][exp]['accounts'][key]['ledger']
    rows.append([label,f(a['portfolio']['equity'],4),pct(float(a['returnPct'])),str(l['completedRoundTrips']),str(a['completedSlots'])])
table(p,['Tài khoản','Equity $','Lãi/lỗ','Vòng đóng','Kỳ xử lý'],rows,[136,96,85,85,90])
para(p,'V2 bắt đầu 15/09; universe bắt đầu tối 19/09. <b>Chỉ so sánh trong từng cặp.</b> Cả 10 backend/timer hoạt động, bốn ledger đối chiếu đạt, không thiếu kỳ hay request tồn. Gần như toàn bộ tài khoản đã về tiền mặt, chỉ còn lượng dư làm tròn không đáng kể.')
para(p,'AI bỏ phiếu 5 lần: 3 veto, 2 approve, không lỗi. Ba veto BTC liên tiếp làm cả BTC và lượt ETH sau đó vào muộn khoảng ba giờ. Với giá thoát tương ứng giống nhau, AI hiện kém đối chứng khoảng <b>$0,07696</b>. Đây là chênh lệch thực tế của hai đường danh mục, không phải ước lượng nhân quả tổng quát cho mọi veto.')
para(p,'Nhánh 5 coin có cùng giao dịch BTC/ETH với nhánh 2 coin, cộng thêm BNB và XRP; hai coin này gây thêm khoảng <b>$0,19413</b> lỗ sau phí. Mức phơi nhiễm trung bình lấy mẫu tăng từ $6,49 lên $10,70, phí từ $0,01985 lên $0,03968. Chưa mua SOL.')
table(p,['Điều đã xác minh','Điều chưa chứng minh'],[
['Coverage, ledger và quyết định nhất quán','AI có khả năng dự báo tốt hơn luật'],
['V2 đã có hai vòng BTC/ETH sinh lời','Lợi nhuận ổn định ở nhiều chế độ thị trường'],
['Universe 5 coin có nhiều giao dịch hơn','Năm coin có hiệu quả điều chỉnh rủi ro tốt hơn']], [240,252])
para(p,'<b>116 kỳ theo giờ không phải 116 quan sát lợi nhuận độc lập.</b> Mỗi nhánh V2 mới có hai vòng mua-bán hoàn tất; nhiều giờ cùng nắm một vị thế. Không tính Sharpe năm hóa hay kiểm định thắng/thua có ý nghĩa từ mẫu forward này.')
small(p,'Nguồn nội bộ: v2-report.json, universe-report.json, audit.json tại data/reviews/status-2026-09-20. Phí paper đã bao gồm; spread, slippage, AI/VPS chưa bao gồm.')

p=page('04  Kiểm tra lại bằng chứng lịch sử cũ')
names=['trend_proxy','trend_no_24h','trend_fast','breakout','mean_reversion','buy_hold']
rows=[]
for name in names:
    m=old(name)['metrics']
    rows.append([name,pct(m['returnPct']),f(m['maxDrawdownPct'])+'%',str(m['closedRoundTrips']),f(m['completedTradeExpectancyUsd'],4) if m['completedTradeExpectancyUsd'] is not None else '-',f(m['riskBlockedPctHours'],1)+'%'])
table(p,['Chiến lược','Return','MDD','Vòng','E[PnL] $','Giờ chặn'],rows,[119,72,70,54,85,92])
small(p,'01/03 - 01/09/2026, 184 ngày, proxy lịch sử, phí 10 bps + chi phí bất lợi 5 bps mỗi chiều. MDD chỉ từ close giờ trong engine cũ. Buy_hold cũ có risk overlay; không kích hoạt trong mẫu base-case này.')
figure(p,'existing-equity-mar-aug.png','Hình 1. Đường equity của sáu tháng lịch sử đã được xem. Đây là dữ liệu phát triển, không phải OOS mới.',220)
para(p,'Đối chiếu độc lập <b>84 lần chạy, 375.984 điểm định giá và 15.555 fills</b> đều khớp. Vấn đề chính là ý nghĩa kinh tế: trend gốc bị chặn khoảng 50% số giờ trong sáu tháng và 59,46% trong cửa sổ 18 tháng trước đó. Lợi nhuận quanh -6% phản ánh cả cơ chế dừng mua, không chỉ chất lượng tín hiệu.')
small(p,'Cash trong cùng kỳ: 0% và 0 drawdown. Chỉ số kỳ vọng/vòng gồm phí của các vòng hoàn tất; vị thế cuối kỳ còn mở vẫn nằm trong equity nhưng không nằm trong kỳ vọng/vòng.')

p=page('05  Replay mới: đường chạy liên tục')
rows=[]
for u in U:
    for v in ['baseline','passive10']:
        m=s('continuous',u,v)
        rows.append([UL[u],VL.get(v,'Mua-giữ $10'),pct(m['returnPct']),f(m['maxDrawdownPct'])+'%',f(m['averageExposureUsd']),f(m['blockedHours']/m['hours']*100,1)+'%'])
table(p,['Universe','Phương án','Return','MDD','Vốn TB $','Giờ chặn'],rows,[104,103,68,68,73,76])
para(p,'Khoảng đánh giá: <b>01/09/2024 - 01/09/2026</b>, tài khoản liên tục với $50. Cash: 0%. Luật gốc 2 coin mất 6,09%; luật gốc 5 coin mất 6,52%, với drawdown lần lượt 10,93% và 18,42%. Ngưỡng $47 từ vốn ban đầu không chặn được mức sụt giảm từ một đỉnh đã tăng cao.')
para(p,'Benchmark mới <b>passive10</b> phân bổ tổng $10 ban đầu đều cho coin trong universe, mỗi giờ mua một coin, không bán và không áp risk overlay. Hai coin mua $5/coin; năm coin mua $2/coin. $40 còn lại giữ cash, trừ phí. Đây là benchmark cùng vốn đầu tư ban đầu, <b>không khớp exposure thực tế</b> theo thời gian.')
para(p,'Trong nhánh 5 coin cap $10, giới hạn áp cho BUY của chiến lược chủ động; benchmark mua-giữ có thể tăng giá vượt $10 và không bị bán ép. Không diễn giải chênh lệch return đơn thuần thành alpha hoặc superior risk-adjusted return.')
table(p,['Nguyên tắc kế toán','Quy ước'],[
['Lãi/lỗ tài khoản','Equity = cash + giá trị coin; PnL = equity - $50, đã trừ phí.'],
['Định giá cuối kỳ','Giữ vị thế mở theo giá thị trường; thêm estimatedNetExitEquity cho chi phí bán giả định.'],
['Dust và bán từng phần','Giữ đủ lượng/cost basis; notional SELL làm tròn xuống 6 chữ số, lượng xuống 24 chữ số.'],
['MDD mới','Đỉnh so với các điểm open, sau fill và close; vẫn không biết đáy trong giờ.']], [155,337])

p=page('06  Độ bền theo thời điểm khởi đầu')
figure(p,'startup-heatmap.png','Hình 2. Mỗi ô là lợi nhuận ròng (%) của một tài khoản mới trong ba tháng; cùng phí và chi phí 5 bps/chiều. Không ghép các ô thành lãi kép.',300)
para(p,'<b>Cả 12 cấu hình chủ động có trung vị lợi nhuận âm.</b> Đa số chỉ lãi ở 3/8 cửa sổ. Cấu hình 5 coin batch_peak chỉ lãi 2/8 dù đường chạy liên tục hai năm báo +11,76%. Điều này cho thấy rủi ro chọn một ngày bắt đầu thuận lợi.')
rows=[]
for u in U:
    a=blocks(u,'baseline'); b=blocks(u,'batch_peak')
    rows.append([UL[u],pct(statistics.median(x['returnPct'] for x in a)),pct(statistics.median(x['returnPct'] for x in b)),f"{sum(x['returnPct']>0 for x in a)}/8",f"{sum(x['returnPct']>0 for x in b)}/8"])
table(p,['Universe','Median gốc','Median lô+đỉnh','Lãi gốc','Lãi lô+đỉnh'],rows,[130,94,100,84,84])
para(p,'Tám cửa sổ không chồng lấn nhưng vẫn thuộc một lịch sử thị trường và có chế độ kế tiếp nhau. Không coi 8 ô là tám thử nghiệm độc lập hoàn toàn; không dùng p-value đơn giản để tuyên bố chiến lược thắng.')

p=page('07  Thoát theo lô và bảo vệ từ đỉnh')
rows=[]
for v in VARIANTS:
    m=s('continuous','five',v)
    rows.append([VL[v],pct(m['returnPct']),f(m['maxDrawdownPct'])+'%',f(m['blockedHours']/m['hours']*100,1)+'%',str(m['exitBacklogHours'])])
table(p,['5 coin / cap $20','Return','MDD','Giờ chặn','Giờ còn chờ'],rows,[164,82,82,82,82])
figure(p,'five-equity-and-dd.png','Hình 3. Chặn từ đỉnh có thể giữ một khoản lời rồi nằm tiền mặt rất lâu. Đồ thị DD dùng close; bảng dùng thêm open và sau fill.',255)
para(p,'Guard 6% dùng đỉnh equity đã quan sát ở close trước và open hiện tại; kiểm tra ngưỡng ở open, bao gồm phí vào lệnh. Khi kích hoạt, chặn BUY vĩnh viễn trong lần chạy đó và bán giảm dần. Không có cơ chế tự mở lại. Vì gap và fill theo giờ, MDD có thể vượt 6%.')
para(p,'Bán theo lô đưa số giờ còn hàng chờ về 0 trên đường liên tục base-case của cả ba universe. Một số kịch bản khác còn chờ do dust sát ngưỡng $0,000001, làm tròn và slippage. Với 5 coin, MDD liên tục tăng từ 18,42% lên 21,18% nếu chỉ đổi batch_exit; <b>không đồng nghĩa luôn an toàn hơn trên toàn đường danh mục</b>.')
small(p,'Ứng viên peak_guard là thử nghiệm bảo vệ rủi ro, không phải nguồn alpha đã chứng minh. Kết quả đẹp do nghỉ 78,6% thời gian cần đọc cùng các cửa sổ khởi đầu mới.')

p=page('08  Universe, exposure và chế độ thị trường')
figure(p,'asset-correlation.png','Hình 4. Tương quan log-return theo giờ quan sát trên cùng tập dữ liệu. Không phải giả định tương quan sẽ giữ nguyên.',230)
para(p,'Năm coin không phải năm nguồn rủi ro độc lập. Bản gốc 5 coin có exposure trung bình cao hơn 2 coin; giới hạn $20 và thứ tự ưu tiên cố định còn tạo cạnh tranh công suất. Nhánh cap $10 kiểm tra một phần tác động này, không tạo exposure khớp hoàn toàn.')
rows=[]
btc={int(r[0]):r for r in raw['BTCUSDT']}
for u in U:
    group={'up':[],'down':[]}
    for m in blocks(u,'baseline'):
        start=int(datetime.fromisoformat(m['start'].replace('Z','+00:00')).timestamp()*1000)
        end=int(datetime.fromisoformat(m['endExclusive'].replace('Z','+00:00')).timestamp()*1000)
        market_ret=float(btc[end-3600000][4])/float(btc[start][1])-1
        group['up' if market_ret>0 else 'down'].append(m['returnPct'])
    rows.append([UL[u],str(len(group['up'])),pct(statistics.mean(group['up'])) if group['up'] else '-',str(len(group['down'])),pct(statistics.mean(group['down'])) if group['down'] else '-'])
table(p,['Luật gốc','Kỳ BTC tăng','Return TB','Kỳ BTC giảm','Return TB'],rows,[140,91,85,91,85])
para(p,'Nhãn BTC tăng/giảm chỉ được biết sau khi hết cửa sổ, dùng mô tả kết quả; không được đưa vào quyết định mua trong mô phỏng. Số cửa sổ ít, không suy ra một bộ lọc regime đã có giá trị dự báo.')
small(p,'Trong 19 ngày gần đây, gốc 5 coin đạt +2,22%, gốc 2 coin -0,27%, 5 coin cap $10 +0,52%. Thử nghiệm forward mới khởi đầu tối 19/09 lại đang lỗ. Hai kết quả không mâu thuẫn: khác ngày bắt đầu và vị thế kế thừa.')

p=page('09  Chi phí: lợi thế còn lại bao nhiêu?')
figure(p,'cost-sensitivity.png','Hình 5. Mỗi điểm chạy lại toàn bộ đường danh mục; không chỉ lấy turnover nhân thêm phí.',210)
rows=[]
for u in U:
    for v in ['baseline','batch_peak']:
        rows.append([UL[u],VL[v],*[pct(s('continuous',u,v,b)['returnPct']) for b in [0,5,10]]])
table(p,['Universe','Phương án','0 bps','5 bps','10 bps'],rows,[130,122,80,80,80])
para(p,'Phí giao dịch luôn là 10 bps mỗi chiều; 1 bp = 0,01%. Cột 0/5/10 bps là spread/slippage bất lợi bổ sung: tổng round-trip danh nghĩa xấp xỉ 20/30/40 bps, chưa gồm chi phí hệ thống. Đây là giả định stress, chưa được hiệu chỉnh bằng dữ liệu order book.')
para(p,'Chi phí cao hơn đôi khi có kết quả cuối kỳ tốt hơn do đổi lượng coin, ngày chạm ngưỡng, capacity và thời điểm dừng mua. <b>Không suy ra chi phí cao là tốt.</b> Báo cáo giữ nguyên các đường phi tuyến thay vì ép kết quả đơn điệu.')
para(p,'Ví dụ cũ: breakout sáu tháng giảm từ +2,33% ở 0 bps còn +1,22% ở 5 bps và +0,12% ở 10 bps. Phí riêng đã tốn $1,0967 trong khi lãi chỉ $0,6092. Đơn vị lệnh $5 khiến AI/VPS có thể rất lớn so với lợi nhuận; chưa có số tiền thực tế để khấu trừ chính xác.')
small(p,'Engine chưa mô phỏng slippage thay đổi theo biến động/thanh khoản, min-notional và lot-size, fill không đủ, mất kết nối, spread giãn hoặc giá gap trong giờ. Các giới hạn này phải được xử lý trước khi gọi là backtest thực thi hoàn chỉnh.')

p=page('10  Bất định thống kê và lựa chọn nhiều lần')
figure(p,'existing-bootstrap-excess.png','Hình 6. Trung bình lợi nhuận ngày vượt benchmark và khoảng percentile 95%; block 7 ngày, 5.000 mẫu. Kỳ 03-08/2026 của engine proxy cũ.',240)
para(p,'Dùng paired circular block bootstrap trên chênh lệch return ngày cùng ngày, block chính 7 ngày; kiểm tra 1 và 14 ngày, seed 20260920. Giữ cấu trúc phụ thuộc ngắn hạn trong từng block; không bootstrap từng giao dịch như quan sát độc lập.')
table(p,['Breakout so với','Excess TB (bp/ngày)','Khoảng 95% (bp/ngày)'],[
['Cash','+0,713','[-3,637; +6,289]'],['Mua-giữ','-1,686','[-6,924; +4,159]']], [148,162,182])
para(p,'Khoảng đều chứa 0: <b>chưa phân biệt được lợi thế dương đáng tin cậy</b>. Breakout có 4/6 tháng âm; ngày tốt nhất đóng góp khoảng $1,098, lớn hơn toàn bộ lãi $0,609 của kỳ. Đây là phân rã PnL, không phải backtest sau khi tùy ý bỏ ngày tốt nhất.')
para(p,'Các khoảng trên chỉ là chẩn đoán có điều kiện trên đường đã quan sát. Bootstrap không chạy lại loss gate theo thứ tự mới; các đoạn dừng mua khiến tính dừng yếu. Không diễn giải khoảng này thành dự báo, xác suất thắng hay bằng chứng đã hiệu chỉnh việc chọn nhiều mô hình.')
small(p,'Sharpe ngày của breakout: 0,410 khi nhân căn 365; xấp xỉ HAC lag 7: 0,398. Serial correlation ảnh hưởng Sharpe [2]. Không công bố DSR/PBO vì chưa có danh mục toàn bộ lần thử lịch sử và giả định đủ tin cậy; nguyên tắc multiple testing/selection bias theo [3], [4].')

p=page('11  Quyết định và protocol forward kế tiếp')
table(p,['Hạng mục','Quyết định hiện tại','Lý do'],[
['Luật vào lệnh gốc','Giữ làm đối chứng paper','Chưa có edge bền, nhưng cần đối chứng cố định.'],
['AI veto','Tiếp tục đánh giá riêng','Chỉ 2 vòng/nhánh; hiện kém đối chứng.'],
['Mở rộng universe','Giữ thử nghiệm độc lập','Exposure và ngày bắt đầu ảnh hưởng lớn.'],
['Bán theo lô','Ứng viên nghiên cứu rủi ro','Hết hàng chờ nhưng return/MDD không nhất quán.'],
['Peak guard 6%','Chưa chọn theo số lãi đẹp','Nhiều thời gian đứng ngoài; phụ thuộc đường đi.'],
['Vốn thật / tăng vốn','Chưa đạt cổng bằng chứng','Chưa có OOS mới, chi phí thực thi chưa đo.']], [119,173,200])
para(p,'<b>Đề xuất lượt kiểm định tới, chưa kích hoạt:</b> chọn đúng một can thiệp có lý do trước khi bắt đầu; ưu tiên kiểm định vận hành của thoát theo lô trong tài khoản riêng. Giữ nguyên entry, universe, vốn, phí và nguồn snapshot giữa ứng viên và đối chứng.')
para(p,'<b>Đăng ký trước:</b> outcome chính là chênh lệch return ngày ròng của cả danh mục; phụ là MDD, hàng chờ thoát, exposure, turnover, coverage và chi phí thực. Không chọn lại outcome sau khi thấy kết quả. Nếu mục tiêu chỉ là rủi ro, phải đăng ký trước mức lợi nhuận hy sinh chấp nhận được; chưa có ngưỡng đó trong nghiên cứu này.')
para(p,'<b>Cổng đánh giá tối thiểu:</b> 90 ngày lịch và 30 vòng đóng mỗi nhánh trước lần xem xét kinh tế đầu tiên; đây là tiêu chí quy trình, không bảo đảm đủ statistical power. Không ép giao dịch để đủ số lượng. Nếu khoảng bất định còn rộng, kết luận là chưa đủ dữ liệu.')
para(p,'Ứng viên muốn được coi là cải thiện lợi nhuận cần excess return dương, khoảng bất định có tính phụ thuộc không bao gồm 0, còn tồn tại ở stress 10 bps, không lỗi đối chiếu sổ hoặc thiếu kỳ không giải thích được. Nếu thử nhiều ứng viên, phải khai báo họ kiểm định và hiệu chỉnh trước; không tự động triển khai.')
small(p,'Không reset hoặc sửa strategy của thí nghiệm đang chạy trong đợt nghiên cứu này. Thay quy tắc giữa kỳ phải mở một segment mới; không gộp kết quả thuận lợi sau khi thay đổi vào lịch sử cũ.')

p=page('12  Tái lập, kiểm tra và tài liệu nguồn')
para(p,'<b>Gói nguồn:</b> quant/research_20260920 gồm protocol, data audit, shared-policy replay, phân tích thống kê, kiểm thử và trình dựng báo cáo. Đầu ra số liệu nằm tại data/quant/research-20260920; snapshot forward ở data/reviews/status-2026-09-20.')
table(p,['Thứ tự','Lệnh từ thư mục dự án'],[
['1. Dữ liệu','python -B quant/research_20260920/data_audit.py'],
['2. Replay','python -B quant/research_20260920/replay.py'],
['3. Thống kê cũ','python -B quant/research_20260920/existing_evidence.py'],
['4. Kiểm thử','python -B -m unittest discover -s quant/research_20260920 -p "test_*.py"'],
['5. Báo cáo','python -B quant/research_20260920/build_report.py']], [83,409])
small(p,'Replay và dựng báo cáo dùng cache nội bộ, không gửi tín hiệu vào bot. data_audit lấy public GET để kiểm tra lại nguồn. Runtime cần Python 3.12+, numpy, matplotlib, reportlab; phiên bản ghi trong research-summary.json và requirements.txt. PDF dùng Arial trên Windows; có thể đặt QUANT_FONT_DIR trỏ tới thư mục font Arial trên máy khác.')
para(p,'<b>Kiểm tra cuối:</b> 18 kiểm thử mới đạt; 20 kiểm thử engine lịch sử đạt. Đã sửa lỗi đếm hàng chờ khi dust được mua lại, bổ sung kiểm tra dữ liệu hữu hạn và đếm giờ chặn do phí chạm ngưỡng đỉnh. Sau chạy lại, toàn bộ fills và PnL của 540 kịch bản <b>không thay đổi</b>. Protocol gốc, bản làm rõ và đầu ra trước sửa đều được giữ để truy vết.')
small(p,'Giờ chặn trong báo cáo mới gồm daily gate, equity floor, peak latch và peak entry-fee floor; không gồm capacity hay thiếu tín hiệu. firstLossHalt trong JSON là lần hạn chế vào đầu tiên, có thể tạm thời do phí; peakBlockedHours mới phản ánh guard đã chốt vĩnh viễn.')
para(p,'<b>Định nghĩa:</b> return = equity cuối / 50 - 1; MDD = max((đỉnh đã quan sát - equity)/đỉnh); return ngày = equity đóng ngày / equity đóng ngày trước - 1; expectancy = trung bình PnL ròng của các vòng hoàn tất; exposure là giá trị coin, không phải leverage. Một vòng bán thành nhiều fill chỉ tính là một vòng đóng khi dưới ngưỡng dust.')
para(p,'<b>Tài liệu tham khảo</b>')
small(p,'[1] Binance. Spot API, Kline/Candlestick data. <link href="https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md">github.com/binance/binance-spot-api-docs</link>. Nguồn dữ liệu và quy ước open time, UTC, giới hạn truy vấn.')
small(p,'[2] Lo, A. W. (2002). The Statistics of Sharpe Ratios. Financial Analysts Journal 58(4), 36-52. <link href="https://doi.org/10.2469/faj.v58.n4.2453">doi:10.2469/faj.v58.n4.2453</link>.')
small(p,'[3] Bailey, Borwein, López de Prado, Zhu (2015). The Probability of Backtest Overfitting. <link href="https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf">davidhbailey.com/dhbpapers/backtest-prob.pdf</link>.')
small(p,'[4] Bailey, López de Prado (2014). The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality. Journal of Portfolio Management 40(5), 94-107. <link href="https://doi.org/10.2139/ssrn.2460551">doi:10.2139/ssrn.2460551</link>.')
small(p,'Các số liệu và biểu đồ do tính toán từ dữ liệu của dự án; tài liệu ngoài chỉ hỗ trợ phương pháp. Không sao chép kết quả hiệu suất từ nguồn ngoài. Ngày truy cập tài liệu: 20/09/2026.')

font_dir=Path(os.environ.get('QUANT_FONT_DIR','C:/Windows/Fonts'))
pdfmetrics.registerFont(TTFont('Arial',str(font_dir/'arial.ttf')))
pdfmetrics.registerFont(TTFont('Arial-Bold',str(font_dir/'arialbd.ttf')))
pdfmetrics.registerFont(TTFont('Arial-Italic',str(font_dir/'ariali.ttf')))
pdfmetrics.registerFontFamily('Arial',normal='Arial',bold='Arial-Bold',italic='Arial-Italic',boldItalic='Arial-Bold')
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='BodyVN',fontName='Arial',fontSize=9.4,leading=13.5,spaceAfter=10,textColor=colors.HexColor(NAVY)))
styles.add(ParagraphStyle(name='SmallVN',fontName='Arial',fontSize=8.1,leading=11.3,spaceAfter=8,textColor=colors.HexColor(GRAY)))
styles.add(ParagraphStyle(name='TitleVN',fontName='Arial-Bold',fontSize=22,leading=28,spaceAfter=19,textColor=colors.HexColor(NAVY)))
styles.add(ParagraphStyle(name='KickerVN',fontName='Arial-Bold',fontSize=8,leading=11,spaceAfter=9,textColor=colors.HexColor(TEAL)))
styles.add(ParagraphStyle(name='CellVN',fontName='Arial',fontSize=8.1,leading=10.7,textColor=colors.HexColor(NAVY)))
styles.add(ParagraphStyle(name='HeadVN',fontName='Arial-Bold',fontSize=8.1,leading=10.7,textColor=colors.white))
story=[]; md=['# Nghiên cứu quant: BTC/ETH và danh mục 5 coin','', 'Ngày: 20/09/2026. Mã nghiên cứu: Q-20260920.','']
def markdown(text):
    import re
    text=text.replace('<b>','**').replace('</b>','**')
    text=re.sub(r'<link href="([^"]+)">([^<]+)</link>',r'[\2](\1)',text)
    return text
for index,pg in enumerate(pages):
    if index: story.append(PageBreak())
    story += [Paragraph(pg['kicker'],styles['KickerVN']),Paragraph(escape(pg['title']).replace('\n','<br/>'),styles['TitleVN'])]
    md += ['## '+pg['title'].replace('\n',' '),'']
    for kind,value in pg['items']:
        if kind in ('p','small'):
            story.append(Paragraph(value,styles['BodyVN' if kind=='p' else 'SmallVN']))
            md += [markdown(value),'']
        elif kind=='table':
            heads,rows,widths=value
            if widths is None: widths=[492/len(heads)]*len(heads)
            cells=[[Paragraph(escape(str(x)).replace('\n','<br/>'),styles['HeadVN']) for x in heads]]
            cells += [[Paragraph(escape(str(x)).replace('\n','<br/>'),styles['CellVN']) for x in row] for row in rows]
            t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(NAVY)),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F0F5F7'),colors.white]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,-1),(-1,-1),.5,colors.HexColor('#C9D4DA'))]))
            story += [t,Spacer(1,11)]
            md += ['| '+' | '.join(heads)+' |','| '+' | '.join(['---']*len(heads))+' |']
            md += ['| '+' | '.join(str(x).replace('\n','; ') for x in row)+' |' for row in rows]; md+=['']
        else:
            path,caption,height=value
            im=Image(str(path)); im._restrictSize(492,height)
            im.hAlign='CENTER'; story += [im,Spacer(1,5),Paragraph(caption,styles['SmallVN'])]
            md += [f'![{caption}](../data/quant/research-20260920/{path.name})','']

pdf=ROOT/'output/pdf/quant-research-20260920.pdf'; pdf.parent.mkdir(parents=True,exist_ok=True)
def page_number(canvas,doc):
    canvas.saveState(); w,h=A4
    canvas.setStrokeColor(colors.HexColor(TEAL)); canvas.setLineWidth(2); canvas.line(42,h-27,w-42,h-27)
    canvas.setFont('Arial',7.5); canvas.setFillColor(colors.HexColor(GRAY))
    canvas.drawString(42,25,'Q-20260920  |  Paper strategy research  |  20/09/2026')
    canvas.drawRightString(w-42,25,str(doc.page)); canvas.restoreState()
doc=SimpleDocTemplate(str(pdf),pagesize=A4,rightMargin=51,leftMargin=51,topMargin=43,bottomMargin=46,
                      title='Nghiên cứu quant BTC/ETH và danh mục 5 coin - 20/09/2026',author='Codex - nghiên cứu theo dữ liệu dự án')
doc.build(story,onFirstPage=page_number,onLaterPages=page_number)
(ROOT/'docs/QUANT_RESEARCH_20260920.md').write_text('\n'.join(md),encoding='utf-8')
summary={'generatedAt':datetime.now(timezone.utc).isoformat(),'newRuns':len(replay['runs']),
         'all12ActiveMedianBlockReturnsNegative':bool((np.median(matrix,axis=1)<0).all()),
         'blockReturnMatrix':matrix.tolist(),'assetCorrelation':corr.tolist(),
         'environment':{'python':sys.version,'numpy':np.__version__,'matplotlib':matplotlib.__version__,'reportlab':__import__('reportlab').Version},
         'pdfSha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
         'inputsSha256':{name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in ['replay-results.json','existing-statistics.json','data-manifest.json']}}
(OUT/'research-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print('PDF',pdf,'sections',len(pages)); print('Markdown',ROOT/'docs/QUANT_RESEARCH_20260920.md')
