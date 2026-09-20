# Nghiên cứu quant: BTC/ETH và danh mục 5 coin

Ngày: 20/09/2026. Mã nghiên cứu: Q-20260920.

## Nghiên cứu quant BTC/ETH và danh mục 5 coin

**Kết luận: chưa chứng minh được lợi thế sinh lời bền vững sau chi phí.** Bot vận hành ổn và V2 đang lãi nhẹ, nhưng bằng chứng forward còn quá ít. Backtest nhiều mốc khởi đầu không xác nhận một phương án thay thế ổn định.

| Dữ liệu | Kiểm định mới | Đối chiếu cũ |
| --- | --- | --- |
| 90.380 nến giờ; 5 coin / hơn 2 năm | 540 kịch bản mô phỏng; 12 cấu hình chủ động | 84 lần chạy cũ; 15.555 fills khớp sổ |

**Phát hiện chính**

1. Cả 12 cấu hình chủ động đều có **trung vị lợi nhuận âm** trên tám cửa sổ ba tháng khởi đầu với $50. Bản gốc 2 coin chỉ lãi ở 3/8 cửa sổ.

2. Một cấu hình 5 coin có chặn sụt giảm từ đỉnh đạt **+11,76%** trên đường chạy hai năm, nhưng ngừng mua khoảng **78,6%** thời gian và chỉ lãi ở **2/8** mốc khởi đầu. Đây là kết quả phụ thuộc đường đi, chưa phải lợi thế đã kiểm chứng.

3. Bán phần đang chờ thoát trong cùng giờ đưa hàng chờ về 0 trên đường liên tục base-case, nhưng **không cải thiện lợi nhuận hoặc drawdown một cách nhất quán**. Một số kịch bản còn lượng dư sát ngưỡng làm tròn.

4. Breakout từng có kết quả dương trong sáu tháng vẫn có khoảng bất định bao gồm 0; lợi nhuận giảm mạnh khi tăng chi phí thực thi. Không đủ cơ sở chọn nó làm chiến lược thắng.

**Quyết định nghiên cứu:** tiếp tục paper, giữ đối chứng hiện tại, ưu tiên đo và kiểm định cơ chế thoát. Chưa đạt tiêu chí chuyển sang vốn thật hoặc tăng rủi ro dựa trên báo cáo này.

Phạm vi: spot long-only, vốn mô phỏng $50, không đòn bẩy. Mốc dữ liệu lịch sử cuối: 20/09/2026 00:00 UTC, loại trừ thời điểm cuối. Snapshot forward: 20/09/2026 khoảng 13:08 UTC+7. Tất cả lịch sử trong báo cáo là dữ liệu phát triển đã được xem; không gọi là OOS chưa chạm.

## 01  Câu hỏi và thiết kế nghiên cứu

Nghiên cứu kiểm tra **lợi thế kinh tế** và **hiệu quả kiểm soát rủi ro** riêng biệt. Một cơ chế giảm lỗ bằng cách dừng giao dịch có thể hữu ích về rủi ro, nhưng không chứng minh khả năng tạo lợi nhuận kỳ vọng dương.

| Giả thuyết | Can thiệp cố định | Tiêu chí quan sát |
| --- | --- | --- |
| H0: chưa có edge dương | Giữ nguyên tín hiệu, phí và giới hạn | Lãi ròng, độ ổn định theo mốc bắt đầu, benchmark |
| H1: thoát theo lô tốt hơn? | Bán hết phần chờ trong cùng giờ; mỗi fill <= $5 | Hàng chờ, drawdown, lợi nhuận và turnover |
| H2: bảo vệ từ đỉnh hữu ích? | Chặn vĩnh viễn khi equity <= 94% đỉnh quan sát | Mức sụt giảm, thời gian bị chặn, cơ hội bỏ lỡ |
| H3: thêm coin tạo lợi ích? | 2 coin vs 5 coin; thêm nhánh 5 coin cap $10 | Tách tác động cơ hội và mức vốn đầu tư |

Thiết kế 2x2 cho cơ chế thoát: **baseline**, **batch_exit**, **peak_guard**, **batch_peak**. Ba universe/cap, ba mức chi phí và 10 cửa sổ tạo 360 kịch bản chủ động. Thêm cash và passive10 tạo tổng cộng 540 lần chạy; các lần chạy trùng benchmark không được coi là bằng chứng độc lập.

| Cửa sổ | Mục đích |
| --- | --- |
| 01/09/2024 - 01/09/2026 | Một tài khoản liên tục; bộc lộ tác động tích lũy và ngừng giao dịch. |
| 8 cửa sổ 3 tháng, không chồng lấn | Mỗi cửa sổ khởi đầu $50 và không vị thế; đo độ nhạy theo ngày bắt đầu. |
| 01/09/2026 - 20/09/2026 | 19 ngày gần đây; chẩn đoán ngắn hạn, không đủ chứng minh độ bền. |

Các cửa sổ ba tháng là **kịch bản khởi đầu mới**, không phải chiến lược tái cấp vốn theo quý. Không nối chúng thành một đường lãi kép. Đây cũng không phải walk-forward huấn luyện mô hình: không có bước fit/optimize trên quá khứ.

Protocol và ngưỡng 6% được ghi trước khi xem kết quả ứng viên mới. Có làm rõ cách kiểm tra ngưỡng ở giá mở cửa trước khi phân tích đầu ra; không thay ngưỡng sau khi xem kết quả. Dữ liệu cũ đã được xem và universe được chọn ở hiện tại, nên nghiên cứu vẫn mang tính khám phá.

## 02  Dữ liệu, nguồn và tính nhân quả

| Kiểm tra dữ liệu | Kết quả |
| --- | --- |
| Nguồn | Binance public spot /api/v3/klines; không cần API key |
| Universe | BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT |
| Độ phủ | 18.076 nến/coin: 100 giờ warm-up + 17.976 giờ đánh giá |
| Tính toàn vẹn | Không gap, không timestamp trùng, OHLCV hợp lệ, không nến chưa đóng |
| Đối chiếu lấy lại | 15 đoạn x 24 giờ lấy lại độc lập theo request; tất cả khớp toàn bộ trường |
| Bảo toàn chứng cứ | SHA-256 của cache, source, protocol, request và response; cache cũ không ghi đè |

**Luồng thông tin mỗi giờ t:** chỉ báo tính từ các nến đã đóng đến t-1; giá mở cửa t làm đại diện cho quote; runner/policy.py quyết định; khớp với chi phí bất lợi; cuối giờ t mới định giá theo close. High/low/close của giờ đang giao dịch không được dùng để quyết định lệnh tại open.

Luật BUY: quote > SMA20 > SMA50 và lợi nhuận 24 giờ của nến đóng > 0. Luật SELL: quote < SMA20 và lợi nhuận 24 giờ < 0, hoặc vi phạm ngân sách lỗ; phần thoát còn lại tiếp tục chờ. Ưu tiên BTC, ETH, SOL, BNB, XRP.

Replay mới gọi trực tiếp **policy đang chạy**, giảm sai lệch do viết lại luật. Tuy vậy, open mỗi giờ chỉ gần đúng quote lúc phút 02; không tái hiện historical spread, latency hay quyết định AI. Các kết quả cũ sử dụng prior close để so với SMA, nên không được trộn như cùng một backtest.

**Giới hạn dữ liệu:** lấy lại từ cùng Binance xác nhận tính nhất quán, không phải xác minh chéo sàn. Chọn năm coin ở hiện tại còn rủi ro selection/survivorship bias. Dữ liệu không chứa độ sâu sổ lệnh, lot-size lịch sử, depeg USDT hay trình tự biến động trong giờ.

Phần 17-19/09 được bổ sung nhưng trạng thái forward đã được xem trước đó; không được gọi là tập kiểm định hoàn toàn chưa biết. Tài liệu nguồn [1]; manifest đầy đủ trong data/quant/research-20260920/data-manifest.json.

## 03  Bằng chứng forward hiện tại

| Tài khoản | Equity $ | Lãi/lỗ | Vòng đóng | Kỳ xử lý |
| --- | --- | --- | --- | --- |
| V2 + AI | 50.4822 | +0.96% | 2 | 116 |
| V2 theo luật | 50.5592 | +1.12% | 2 | 116 |
| Universe 2 coin | 49.8347 | -0.33% | 2 | 20 |
| Universe 5 coin | 49.6405 | -0.72% | 4 | 20 |

V2 bắt đầu 15/09; universe bắt đầu tối 19/09. **Chỉ so sánh trong từng cặp.** Cả 10 backend/timer hoạt động, bốn ledger đối chiếu đạt, không thiếu kỳ hay request tồn. Gần như toàn bộ tài khoản đã về tiền mặt, chỉ còn lượng dư làm tròn không đáng kể.

AI bỏ phiếu 5 lần: 3 veto, 2 approve, không lỗi. Ba veto BTC liên tiếp làm cả BTC và lượt ETH sau đó vào muộn khoảng ba giờ. Với giá thoát tương ứng giống nhau, AI hiện kém đối chứng khoảng **$0,07696**. Đây là chênh lệch thực tế của hai đường danh mục, không phải ước lượng nhân quả tổng quát cho mọi veto.

Nhánh 5 coin có cùng giao dịch BTC/ETH với nhánh 2 coin, cộng thêm BNB và XRP; hai coin này gây thêm khoảng **$0,19413** lỗ sau phí. Mức phơi nhiễm trung bình lấy mẫu tăng từ $6,49 lên $10,70, phí từ $0,01985 lên $0,03968. Chưa mua SOL.

| Điều đã xác minh | Điều chưa chứng minh |
| --- | --- |
| Coverage, ledger và quyết định nhất quán | AI có khả năng dự báo tốt hơn luật |
| V2 đã có hai vòng BTC/ETH sinh lời | Lợi nhuận ổn định ở nhiều chế độ thị trường |
| Universe 5 coin có nhiều giao dịch hơn | Năm coin có hiệu quả điều chỉnh rủi ro tốt hơn |

**116 kỳ theo giờ không phải 116 quan sát lợi nhuận độc lập.** Mỗi nhánh V2 mới có hai vòng mua-bán hoàn tất; nhiều giờ cùng nắm một vị thế. Không tính Sharpe năm hóa hay kiểm định thắng/thua có ý nghĩa từ mẫu forward này.

Nguồn nội bộ: v2-report.json, universe-report.json, audit.json tại data/reviews/status-2026-09-20. Phí paper đã bao gồm; spread, slippage, AI/VPS chưa bao gồm.

## 04  Kiểm tra lại bằng chứng lịch sử cũ

| Chiến lược | Return | MDD | Vòng | E[PnL] $ | Giờ chặn |
| --- | --- | --- | --- | --- | --- |
| trend_proxy | -6.01% | 6.48% | 93 | -0.0323 | 50.0% |
| trend_no_24h | -6.03% | 6.03% | 97 | -0.0311 | 55.6% |
| trend_fast | -6.07% | 7.17% | 225 | -0.0135 | 49.4% |
| breakout | +1.22% | 3.83% | 109 | 0.0058 | 0.0% |
| mean_reversion | -6.14% | 6.46% | 85 | -0.0361 | 47.5% |
| buy_hold | +4.27% | 7.73% | 0 | - | 0.0% |

01/03 - 01/09/2026, 184 ngày, proxy lịch sử, phí 10 bps + chi phí bất lợi 5 bps mỗi chiều. MDD chỉ từ close giờ trong engine cũ. Buy_hold cũ có risk overlay; không kích hoạt trong mẫu base-case này.

![Hình 1. Đường equity của sáu tháng lịch sử đã được xem. Đây là dữ liệu phát triển, không phải OOS mới.](../data/quant/research-20260920/existing-equity-mar-aug.png)

Đối chiếu độc lập **84 lần chạy, 375.984 điểm định giá và 15.555 fills** đều khớp. Vấn đề chính là ý nghĩa kinh tế: trend gốc bị chặn khoảng 50% số giờ trong sáu tháng và 59,46% trong cửa sổ 18 tháng trước đó. Lợi nhuận quanh -6% phản ánh cả cơ chế dừng mua, không chỉ chất lượng tín hiệu.

Cash trong cùng kỳ: 0% và 0 drawdown. Chỉ số kỳ vọng/vòng gồm phí của các vòng hoàn tất; vị thế cuối kỳ còn mở vẫn nằm trong equity nhưng không nằm trong kỳ vọng/vòng.

## 05  Replay mới: đường chạy liên tục

| Universe | Phương án | Return | MDD | Vốn TB $ | Giờ chặn |
| --- | --- | --- | --- | --- | --- |
| 2 coin / cap $20 | Gốc | -6.09% | 10.93% | 1.14 | 69.6% |
| 2 coin / cap $20 | Mua-giữ $10 | +3.14% | 20.10% | 12.92 | 0.0% |
| 5 coin / cap $20 | Gốc | -6.52% | 18.42% | 4.75 | 37.5% |
| 5 coin / cap $20 | Mua-giữ $10 | +7.42% | 25.77% | 16.76 | 0.0% |
| 5 coin / cap $10 | Gốc | -5.99% | 21.07% | 3.20 | 15.4% |
| 5 coin / cap $10 | Mua-giữ $10 | +7.42% | 25.77% | 16.76 | 0.0% |

Khoảng đánh giá: **01/09/2024 - 01/09/2026**, tài khoản liên tục với $50. Cash: 0%. Luật gốc 2 coin mất 6,09%; luật gốc 5 coin mất 6,52%, với drawdown lần lượt 10,93% và 18,42%. Ngưỡng $47 từ vốn ban đầu không chặn được mức sụt giảm từ một đỉnh đã tăng cao.

Benchmark mới **passive10** phân bổ tổng $10 ban đầu đều cho coin trong universe, mỗi giờ mua một coin, không bán và không áp risk overlay. Hai coin mua $5/coin; năm coin mua $2/coin. $40 còn lại giữ cash, trừ phí. Đây là benchmark cùng vốn đầu tư ban đầu, **không khớp exposure thực tế** theo thời gian.

Trong nhánh 5 coin cap $10, giới hạn áp cho BUY của chiến lược chủ động; benchmark mua-giữ có thể tăng giá vượt $10 và không bị bán ép. Không diễn giải chênh lệch return đơn thuần thành alpha hoặc superior risk-adjusted return.

| Nguyên tắc kế toán | Quy ước |
| --- | --- |
| Lãi/lỗ tài khoản | Equity = cash + giá trị coin; PnL = equity - $50, đã trừ phí. |
| Định giá cuối kỳ | Giữ vị thế mở theo giá thị trường; thêm estimatedNetExitEquity cho chi phí bán giả định. |
| Dust và bán từng phần | Giữ đủ lượng/cost basis; notional SELL làm tròn xuống 6 chữ số, lượng xuống 24 chữ số. |
| MDD mới | Đỉnh so với các điểm open, sau fill và close; vẫn không biết đáy trong giờ. |

## 06  Độ bền theo thời điểm khởi đầu

![Hình 2. Mỗi ô là lợi nhuận ròng (%) của một tài khoản mới trong ba tháng; cùng phí và chi phí 5 bps/chiều. Không ghép các ô thành lãi kép.](../data/quant/research-20260920/startup-heatmap.png)

**Cả 12 cấu hình chủ động có trung vị lợi nhuận âm.** Đa số chỉ lãi ở 3/8 cửa sổ. Cấu hình 5 coin batch_peak chỉ lãi 2/8 dù đường chạy liên tục hai năm báo +11,76%. Điều này cho thấy rủi ro chọn một ngày bắt đầu thuận lợi.

| Universe | Median gốc | Median lô+đỉnh | Lãi gốc | Lãi lô+đỉnh |
| --- | --- | --- | --- | --- |
| 2 coin / cap $20 | -2.08% | -2.05% | 3/8 | 3/8 |
| 5 coin / cap $20 | -5.35% | -2.88% | 3/8 | 2/8 |
| 5 coin / cap $10 | -1.83% | -1.90% | 3/8 | 3/8 |

Tám cửa sổ không chồng lấn nhưng vẫn thuộc một lịch sử thị trường và có chế độ kế tiếp nhau. Không coi 8 ô là tám thử nghiệm độc lập hoàn toàn; không dùng p-value đơn giản để tuyên bố chiến lược thắng.

## 07  Thoát theo lô và bảo vệ từ đỉnh

| 5 coin / cap $20 | Return | MDD | Giờ chặn | Giờ còn chờ |
| --- | --- | --- | --- | --- |
| Gốc | -6.52% | 18.42% | 37.5% | 409 |
| Bán theo lô | -6.07% | 21.18% | 34.1% | 0 |
| Chặn từ đỉnh | +7.14% | 6.48% | 78.6% | 142 |
| Lô + từ đỉnh | +11.76% | 6.21% | 78.6% | 0 |

![Hình 3. Chặn từ đỉnh có thể giữ một khoản lời rồi nằm tiền mặt rất lâu. Đồ thị DD dùng close; bảng dùng thêm open và sau fill.](../data/quant/research-20260920/five-equity-and-dd.png)

Guard 6% dùng đỉnh equity đã quan sát ở close trước và open hiện tại; kiểm tra ngưỡng ở open, bao gồm phí vào lệnh. Khi kích hoạt, chặn BUY vĩnh viễn trong lần chạy đó và bán giảm dần. Không có cơ chế tự mở lại. Vì gap và fill theo giờ, MDD có thể vượt 6%.

Bán theo lô đưa số giờ còn hàng chờ về 0 trên đường liên tục base-case của cả ba universe. Một số kịch bản khác còn chờ do dust sát ngưỡng $0,000001, làm tròn và slippage. Với 5 coin, MDD liên tục tăng từ 18,42% lên 21,18% nếu chỉ đổi batch_exit; **không đồng nghĩa luôn an toàn hơn trên toàn đường danh mục**.

Ứng viên peak_guard là thử nghiệm bảo vệ rủi ro, không phải nguồn alpha đã chứng minh. Kết quả đẹp do nghỉ 78,6% thời gian cần đọc cùng các cửa sổ khởi đầu mới.

## 08  Universe, exposure và chế độ thị trường

![Hình 4. Tương quan log-return theo giờ quan sát trên cùng tập dữ liệu. Không phải giả định tương quan sẽ giữ nguyên.](../data/quant/research-20260920/asset-correlation.png)

Năm coin không phải năm nguồn rủi ro độc lập. Bản gốc 5 coin có exposure trung bình cao hơn 2 coin; giới hạn $20 và thứ tự ưu tiên cố định còn tạo cạnh tranh công suất. Nhánh cap $10 kiểm tra một phần tác động này, không tạo exposure khớp hoàn toàn.

| Luật gốc | Kỳ BTC tăng | Return TB | Kỳ BTC giảm | Return TB |
| --- | --- | --- | --- | --- |
| 2 coin / cap $20 | 5 | -0.51% | 3 | -3.38% |
| 5 coin / cap $20 | 5 | +1.05% | 3 | -5.59% |
| 5 coin / cap $10 | 5 | +0.31% | 3 | -2.25% |

Nhãn BTC tăng/giảm chỉ được biết sau khi hết cửa sổ, dùng mô tả kết quả; không được đưa vào quyết định mua trong mô phỏng. Số cửa sổ ít, không suy ra một bộ lọc regime đã có giá trị dự báo.

Trong 19 ngày gần đây, gốc 5 coin đạt +2,22%, gốc 2 coin -0,27%, 5 coin cap $10 +0,52%. Thử nghiệm forward mới khởi đầu tối 19/09 lại đang lỗ. Hai kết quả không mâu thuẫn: khác ngày bắt đầu và vị thế kế thừa.

## 09  Chi phí: lợi thế còn lại bao nhiêu?

![Hình 5. Mỗi điểm chạy lại toàn bộ đường danh mục; không chỉ lấy turnover nhân thêm phí.](../data/quant/research-20260920/cost-sensitivity.png)

| Universe | Phương án | 0 bps | 5 bps | 10 bps |
| --- | --- | --- | --- | --- |
| 2 coin / cap $20 | Gốc | -6.01% | -6.09% | -6.03% |
| 2 coin / cap $20 | Lô + từ đỉnh | -0.30% | -0.80% | -1.43% |
| 5 coin / cap $20 | Gốc | -6.07% | -6.52% | -6.45% |
| 5 coin / cap $20 | Lô + từ đỉnh | +9.76% | +11.76% | +6.56% |
| 5 coin / cap $10 | Gốc | +1.52% | -5.99% | -6.08% |
| 5 coin / cap $10 | Lô + từ đỉnh | +11.81% | +11.39% | +9.75% |

Phí giao dịch luôn là 10 bps mỗi chiều; 1 bp = 0,01%. Cột 0/5/10 bps là spread/slippage bất lợi bổ sung: tổng round-trip danh nghĩa xấp xỉ 20/30/40 bps, chưa gồm chi phí hệ thống. Đây là giả định stress, chưa được hiệu chỉnh bằng dữ liệu order book.

Chi phí cao hơn đôi khi có kết quả cuối kỳ tốt hơn do đổi lượng coin, ngày chạm ngưỡng, capacity và thời điểm dừng mua. **Không suy ra chi phí cao là tốt.** Báo cáo giữ nguyên các đường phi tuyến thay vì ép kết quả đơn điệu.

Ví dụ cũ: breakout sáu tháng giảm từ +2,33% ở 0 bps còn +1,22% ở 5 bps và +0,12% ở 10 bps. Phí riêng đã tốn $1,0967 trong khi lãi chỉ $0,6092. Đơn vị lệnh $5 khiến AI/VPS có thể rất lớn so với lợi nhuận; chưa có số tiền thực tế để khấu trừ chính xác.

Engine chưa mô phỏng slippage thay đổi theo biến động/thanh khoản, min-notional và lot-size, fill không đủ, mất kết nối, spread giãn hoặc giá gap trong giờ. Các giới hạn này phải được xử lý trước khi gọi là backtest thực thi hoàn chỉnh.

## 10  Bất định thống kê và lựa chọn nhiều lần

![Hình 6. Trung bình lợi nhuận ngày vượt benchmark và khoảng percentile 95%; block 7 ngày, 5.000 mẫu. Kỳ 03-08/2026 của engine proxy cũ.](../data/quant/research-20260920/existing-bootstrap-excess.png)

Dùng paired circular block bootstrap trên chênh lệch return ngày cùng ngày, block chính 7 ngày; kiểm tra 1 và 14 ngày, seed 20260920. Giữ cấu trúc phụ thuộc ngắn hạn trong từng block; không bootstrap từng giao dịch như quan sát độc lập.

| Breakout so với | Excess TB (bp/ngày) | Khoảng 95% (bp/ngày) |
| --- | --- | --- |
| Cash | +0,713 | [-3,637; +6,289] |
| Mua-giữ | -1,686 | [-6,924; +4,159] |

Khoảng đều chứa 0: **chưa phân biệt được lợi thế dương đáng tin cậy**. Breakout có 4/6 tháng âm; ngày tốt nhất đóng góp khoảng $1,098, lớn hơn toàn bộ lãi $0,609 của kỳ. Đây là phân rã PnL, không phải backtest sau khi tùy ý bỏ ngày tốt nhất.

Các khoảng trên chỉ là chẩn đoán có điều kiện trên đường đã quan sát. Bootstrap không chạy lại loss gate theo thứ tự mới; các đoạn dừng mua khiến tính dừng yếu. Không diễn giải khoảng này thành dự báo, xác suất thắng hay bằng chứng đã hiệu chỉnh việc chọn nhiều mô hình.

Sharpe ngày của breakout: 0,410 khi nhân căn 365; xấp xỉ HAC lag 7: 0,398. Serial correlation ảnh hưởng Sharpe [2]. Không công bố DSR/PBO vì chưa có danh mục toàn bộ lần thử lịch sử và giả định đủ tin cậy; nguyên tắc multiple testing/selection bias theo [3], [4].

## 11  Quyết định và protocol forward kế tiếp

| Hạng mục | Quyết định hiện tại | Lý do |
| --- | --- | --- |
| Luật vào lệnh gốc | Giữ làm đối chứng paper | Chưa có edge bền, nhưng cần đối chứng cố định. |
| AI veto | Tiếp tục đánh giá riêng | Chỉ 2 vòng/nhánh; hiện kém đối chứng. |
| Mở rộng universe | Giữ thử nghiệm độc lập | Exposure và ngày bắt đầu ảnh hưởng lớn. |
| Bán theo lô | Ứng viên nghiên cứu rủi ro | Hết hàng chờ nhưng return/MDD không nhất quán. |
| Peak guard 6% | Chưa chọn theo số lãi đẹp | Nhiều thời gian đứng ngoài; phụ thuộc đường đi. |
| Vốn thật / tăng vốn | Chưa đạt cổng bằng chứng | Chưa có OOS mới, chi phí thực thi chưa đo. |

**Đề xuất lượt kiểm định tới, chưa kích hoạt:** chọn đúng một can thiệp có lý do trước khi bắt đầu; ưu tiên kiểm định vận hành của thoát theo lô trong tài khoản riêng. Giữ nguyên entry, universe, vốn, phí và nguồn snapshot giữa ứng viên và đối chứng.

**Đăng ký trước:** outcome chính là chênh lệch return ngày ròng của cả danh mục; phụ là MDD, hàng chờ thoát, exposure, turnover, coverage và chi phí thực. Không chọn lại outcome sau khi thấy kết quả. Nếu mục tiêu chỉ là rủi ro, phải đăng ký trước mức lợi nhuận hy sinh chấp nhận được; chưa có ngưỡng đó trong nghiên cứu này.

**Cổng đánh giá tối thiểu:** 90 ngày lịch và 30 vòng đóng mỗi nhánh trước lần xem xét kinh tế đầu tiên; đây là tiêu chí quy trình, không bảo đảm đủ statistical power. Không ép giao dịch để đủ số lượng. Nếu khoảng bất định còn rộng, kết luận là chưa đủ dữ liệu.

Ứng viên muốn được coi là cải thiện lợi nhuận cần excess return dương, khoảng bất định có tính phụ thuộc không bao gồm 0, còn tồn tại ở stress 10 bps, không lỗi đối chiếu sổ hoặc thiếu kỳ không giải thích được. Nếu thử nhiều ứng viên, phải khai báo họ kiểm định và hiệu chỉnh trước; không tự động triển khai.

Không reset hoặc sửa strategy của thí nghiệm đang chạy trong đợt nghiên cứu này. Thay quy tắc giữa kỳ phải mở một segment mới; không gộp kết quả thuận lợi sau khi thay đổi vào lịch sử cũ.

## 12  Tái lập, kiểm tra và tài liệu nguồn

**Gói nguồn:** quant/research_20260920 gồm protocol, data audit, shared-policy replay, phân tích thống kê, kiểm thử và trình dựng báo cáo. Đầu ra số liệu nằm tại data/quant/research-20260920; snapshot forward ở data/reviews/status-2026-09-20.

| Thứ tự | Lệnh từ thư mục dự án |
| --- | --- |
| 1. Dữ liệu | python -B quant/research_20260920/data_audit.py |
| 2. Replay | python -B quant/research_20260920/replay.py |
| 3. Thống kê cũ | python -B quant/research_20260920/existing_evidence.py |
| 4. Kiểm thử | python -B -m unittest discover -s quant/research_20260920 -p "test_*.py" |
| 5. Báo cáo | python -B quant/research_20260920/build_report.py |

Replay và dựng báo cáo dùng cache nội bộ, không gửi tín hiệu vào bot. data_audit lấy public GET để kiểm tra lại nguồn. Runtime cần Python 3.12+, numpy, matplotlib, reportlab; phiên bản ghi trong research-summary.json và requirements.txt. PDF dùng Arial trên Windows; có thể đặt QUANT_FONT_DIR trỏ tới thư mục font Arial trên máy khác.

**Kiểm tra cuối:** 18 kiểm thử mới đạt; 20 kiểm thử engine lịch sử đạt. Đã sửa lỗi đếm hàng chờ khi dust được mua lại, bổ sung kiểm tra dữ liệu hữu hạn và đếm giờ chặn do phí chạm ngưỡng đỉnh. Sau chạy lại, toàn bộ fills và PnL của 540 kịch bản **không thay đổi**. Protocol gốc, bản làm rõ và đầu ra trước sửa đều được giữ để truy vết.

Giờ chặn trong báo cáo mới gồm daily gate, equity floor, peak latch và peak entry-fee floor; không gồm capacity hay thiếu tín hiệu. firstLossHalt trong JSON là lần hạn chế vào đầu tiên, có thể tạm thời do phí; peakBlockedHours mới phản ánh guard đã chốt vĩnh viễn.

**Định nghĩa:** return = equity cuối / 50 - 1; MDD = max((đỉnh đã quan sát - equity)/đỉnh); return ngày = equity đóng ngày / equity đóng ngày trước - 1; expectancy = trung bình PnL ròng của các vòng hoàn tất; exposure là giá trị coin, không phải leverage. Một vòng bán thành nhiều fill chỉ tính là một vòng đóng khi dưới ngưỡng dust.

**Tài liệu tham khảo**

[1] Binance. Spot API, Kline/Candlestick data. [github.com/binance/binance-spot-api-docs](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md). Nguồn dữ liệu và quy ước open time, UTC, giới hạn truy vấn.

[2] Lo, A. W. (2002). The Statistics of Sharpe Ratios. Financial Analysts Journal 58(4), 36-52. [doi:10.2469/faj.v58.n4.2453](https://doi.org/10.2469/faj.v58.n4.2453).

[3] Bailey, Borwein, López de Prado, Zhu (2015). The Probability of Backtest Overfitting. [davidhbailey.com/dhbpapers/backtest-prob.pdf](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

[4] Bailey, López de Prado (2014). The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality. Journal of Portfolio Management 40(5), 94-107. [doi:10.2139/ssrn.2460551](https://doi.org/10.2139/ssrn.2460551).

Các số liệu và biểu đồ do tính toán từ dữ liệu của dự án; tài liệu ngoài chỉ hỗ trợ phương pháp. Không sao chép kết quả hiệu suất từ nguồn ngoài. Ngày truy cập tài liệu: 20/09/2026.
