# Chiến lược quant, trading bot và vai trò của AI

Ngày khảo sát: **20/09/2026**. Phạm vi: nguồn công khai của quỹ, công ty giao dịch, sàn, phần mềm và nghiên cứu gốc. Đây là khảo sát để chọn hướng nghiên cứu tiếp theo; không phải backtest mới. Không thay đổi chiến lược hoặc cấu hình vận hành.

**Kết luận nghiên cứu**

Các chiến lược có cơ sở kinh tế thuộc một số nhóm: khai thác xu hướng; lựa chọn tài sản tương đối; cung cấp thanh khoản; khai thác chênh lệch giá/carry; cung cấp bảo hiểm qua quyền chọn; khai thác thông tin. AI giúp nghiên cứu, đo lường và triển khai các cơ chế này. Tên mô hình, số lượng agent hay khả năng giải thích trôi chảy không tự tạo ra lợi nhuận kỳ vọng dương.

Chọn hướng phù hợp hạ tầng và dễ kiểm chứng là quyết định về thiết kế nghiên cứu; chưa phải kết luận rằng hướng đó sẽ sinh lời. Với bot hiện tại, ưu tiên nghiên cứu chiến lược spot có turnover thấp và tính tăng thêm của dữ liệu mới. Market making tốc độ cao, arbitrage nhiều sàn và derivatives carry cần hệ thống khác.

**Bằng chứng nào đủ để nói “đang kiếm tiền”?**

| Loại bằng chứng | Có thể kết luận | Không thể kết luận |
|---|---|---|
| Hiệu suất sản phẩm công khai, có ngày và share class | Sản phẩm đã có kết quả được công bố trong kỳ đó | Toàn công ty có cùng lợi nhuận; từng tín hiệu đều tạo alpha; có thể sao chép sang crypto |
| Công ty mô tả hoạt động, công nghệ | Họ công khai sử dụng/nghiên cứu phương pháp đó | Công thức bí mật, trọng số hiện tại, PnL từng chiến lược |
| Nghiên cứu và mô phỏng | Kết quả trong dữ liệu, mô hình và giả định đã nêu | Lợi nhuận live hiện tại sau mọi chi phí |
| Tài liệu bot và màn hình APR | Cách phần mềm đặt lệnh, tính số liệu | Lợi thế bền vững của người dùng bot |

Hai ví dụ có mốc hiện tại rõ ràng, cùng đến **31/08/2026**:

| Sản phẩm | Từ đầu năm 2026 | Bình quân năm trong 5 năm | Bình quân năm trong 10 năm |
|---|---:|---:|---:|
| AQR Managed Futures, AQMIX | +12,96% | +14,49% | +4,88% |
| AQR Equity Market Neutral, QMNIX | −3,60% | +19,73% | +6,54% |

Nguồn: trang hiệu suất [AQMIX](https://funds.aqr.com/funds/aqr-managed-futures-strategy-fund) và [QMNIX](https://funds.aqr.com/funds/aqr-equity-market-neutral-fund). YTD là lợi nhuận lũy kế; các kỳ nhiều năm được thường niên hóa. Đây là kết quả sản phẩm do nhà quản lý công bố, không phải dự báo. Bảng cũng cho thấy một chiến lược có lịch sử dài hạn dương vẫn có thể lỗ trong năm hiện tại.

**Các cơ chế đang được sử dụng hoặc công khai cung cấp**

| Nhóm | Ví dụ và cơ chế | Cái giá phải trả | Khả năng học theo trong dự án |
|---|---|---|---|
| Trend following đa thị trường | AQR công khai long thị trường có xu hướng tăng, short xu hướng giảm trên nhiều loại tài sản. Nguồn: [Managed Futures](https://funds.aqr.com/funds/aqr-managed-futures-strategy-fund). | Đảo chiều gây chuỗi lỗ; cần phân bổ rủi ro, dữ liệu dài và chi phí hợp đồng. | Học cách phối hợp thời hạn và sizing. Năm coin tương quan cao, chỉ mua spot, chưa tái tạo được mức đa dạng hóa đó. |
| Lựa chọn tương đối, multifactor/market neutral | AQR công khai mua cổ phiếu kỳ vọng tốt hơn và bán khống nhóm kém hơn bằng nhiều tín hiệu. Nguồn: [Equity Market Neutral](https://funds.aqr.com/funds/aqr-equity-market-neutral-fund). | Sai mô hình, các vị thế cùng chiều bị tháo gỡ, chi phí short và turnover. | Có thể thử xếp hạng sức mạnh tương đối long-only; không gọi đó là market neutral. |
| Market making | Jane Street công khai cung cấp thanh khoản và dùng ML trong pricing/trading. Cơ chế thu spread phải đi cùng quản lý inventory và hedge. Nguồn: [Jane Street](https://www.janestreet.com/what-we-do/overview/). | Bị giao dịch đối ứng bởi bên có thông tin tốt hơn; giá báo chậm, fill bất lợi và chi phí hedge. | Kém phù hợp polling theo giờ. Cần dữ liệu order book, mô hình hàng đợi/khớp lệnh và cập nhật liên tục. |
| Arbitrage nhiều địa điểm | Hummingbot mô tả đặt maker order ở một sàn rồi hedge tại sàn khác. Nguồn: [XEMM](https://hummingbot.org/strategies/v1-strategies/cross-exchange-market-making/). | Hai chân không khớp đồng thời, vốn nằm ở nhiều nơi, phí và rủi ro đối tác. | Chỉ nên nghiên cứu khi có dữ liệu bid/ask đồng bộ và mô phỏng cả hai chân. Docs phần mềm không chứng minh PnL. |
| Basis và funding carry | Coinbase nghiên cứu spot–futures cash-and-carry; Deribit quy định chuyển funding giữa long/short. Nguồn: [Coinbase, 12/02/2025](https://www.coinbase.com/institutional/research-insights/research/monthly-outlook/monthly-outlook-feb-2025), [Deribit, cập nhật 27/07/2026](https://support.deribit.com/hc/en-us/articles/31424939178397-Funding-Specifications). | Margin, funding đổi dấu, basis mở rộng, financing, settlement và rủi ro sàn. | Có thể mở nhánh nghiên cứu dữ liệu riêng. Bot spot-only hiện tại không thực hiện được hedge này. |
| Options/volatility | GSR công khai cung cấp put/call và cấu trúc phòng hộ. Một cơ chế trong nhóm này là nhận premium để gánh rủi ro mà bên mua muốn chuyển giao. Nguồn: [GSR](https://www.gsr.io/custom-cryptocurrency-options-trading), [nghiên cứu AQR về volatility premium, 2018](https://www.aqr.com/Insights/Research/White-Papers/Understanding-the-Volatility-Risk-Premium). | Rủi ro đuôi lớn, độ cong payoff, hedge và collateral. | Ngoài phạm vi bước kế tiếp. Việc GSR cung cấp options không chứng minh họ chỉ bán volatility hoặc tiết lộ PnL của họ. |

Basis futures kỳ hạn và funding perpetual là hai cơ chế khác nhau. Mua spot/short futures kỳ hạn nhằm hưởng chênh lệch hội tụ, sau chi phí tài trợ và thực thi. Với perpetual, funding tương lai thay đổi; funding dương khiến long trả short, âm thì chiều trả tiền đảo lại. Giảm rủi ro hướng giá không xóa rủi ro margin hoặc mất khả năng thanh toán. Không lấy yield trong bài Coinbase năm 2025 làm yield hiện tại.

ML và dữ liệu thay thế có thể nằm trong nhiều nhóm trên. Two Sigma công khai quy trình đi từ dữ liệu tới mô hình, danh mục và thực thi; đây là một hệ thống kết hợp nhiều bước, không đủ thông tin để sao chép alpha riêng của họ. [Two Sigma Investment Management](https://www.twosigma.com/businesses/investment-management/).

**Grid, DCA và “AI bot” bán lẻ**

Grid tự động mua/bán trong khoảng giá. Binance tính tổng lợi nhuận bằng lãi grid cộng PnL chưa thực hiện; vì vậy các vòng giao dịch có lãi vẫn có thể đi cùng tài khoản lỗ do inventory giảm giá. APR hiển thị được ngoại suy từ thời gian chạy. [Binance Spot Grid, cập nhật 07/01/2026](https://www.binance.com/en/support/faq/detail/688ff6ff08734848915de76a07b953dd).

Spot DCA của Binance có cơ chế mua thêm khi giá giảm và có thể tăng kích thước lệnh; nó khác mua định kỳ cố định. Quy tắc bình quân giá thay đổi mức phơi nhiễm, nhưng không chứng minh giá sẽ hồi trước khi tài khoản cạn tiền. [Binance Spot DCA, cập nhật 17/03/2026](https://www.binance.com/en/support/faq/detail/27713d3ddb3c406da52f36b9aaaa1360).

Do đó, một phép so sánh bot hợp lệ phải dùng toàn bộ equity: tiền mặt, giá trị inventory, phí và chi phí vận hành. Cần đối chiếu mức rủi ro và vốn sử dụng. Win rate, số vòng lời, lợi nhuận đã chốt hoặc APR riêng lẻ đều thiếu thông tin.

**Các công ty đang dùng AI mạnh như thế nào?**

| Nguồn gốc, ngày | Điều được công bố | Giới hạn của bằng chứng |
|---|---|---|
| [Man AHL AlphaTrend, 11/02/2026](https://www.man.com/insights/alphatrend-agentic-research-workflows) | Agent đề xuất, triển khai và đánh giá ý tưởng theo quy trình nghiên cứu có cấu trúc. | Các biểu đồ thử nghiệm trong bài là mô phỏng minh họa, dữ liệu đến 2015; không phải PnL live năm 2026. |
| [Two Sigma, 09/07/2026](https://www.twosigma.com/articles/anything-can-be-language-now-my-thoughts-on-the-future-of-features-research/) | Đầu ra LLM có thể trở thành dữ liệu và feature để kiểm định. | Không công bố lợi nhuận riêng do LLM tạo ra. |
| [Optiver, 27/04/2026](https://www.optiver.com/insights/technology-blog/where-ai-trading-models-work-and-where-they-still-fall-short/) | LLM hữu ích cho phân tích/prototype, nhưng các mô hình được thử còn thiếu nhất quán khi cập nhật niềm tin và hành động qua nhiều bước. | Đây là thí nghiệm nội bộ với các mô hình cụ thể; không phải kết luận rằng mọi mô hình mới đều có cùng kết quả. |

Two Sigma cũng cảnh báo AI tăng số lượng giả thuyết sẽ làm vấn đề chọn trúng kết quả may mắn nặng hơn; LLM có thể đã biết các biến cố nằm trong giai đoạn backtest. Nguồn: [2026 Outlook Part II, 21/01/2026](https://www.twosigma.com/articles/ai-in-investment-management-2026-outlook-part-ii/).

Các benchmark học thuật cần đọc kỹ thiết kế và bảng kết quả. Ghi chú nguồn riêng đã ghi nhận giới hạn của FinBen, StockBench, TradingAgents và nghiên cứu kết hợp tin tức với dự báo volatility: [AI evidence notebook](D:/Code/bot/docs/research_sources_ai_20260920.md). Chưa có bằng chứng trong tập nguồn này cho kết luận “LLM càng mạnh thì lợi nhuận giao dịch càng cao”.

**Kiến trúc đề xuất cho bot — suy luận của nghiên cứu này**

```text
Dữ liệu giá/khối lượng + sự kiện từ nguồn gốc
                    |
          Kiểm tra chất lượng và thời điểm
                    |
       AI trích xuất sự kiện thành biến đo được
                    |
     Mô hình số dự báo lợi nhuận/rủi ro/chi phí
                    |
        Quy tắc danh mục và giới hạn bằng code
                    |
          Thực thi lệnh và đối chiếu tài khoản
                    |
            Theo dõi, lưu log, đánh giá

AI nghiên cứu và reviewer hỗ trợ từng bước ở môi trường thử nghiệm.
```

Vai trò có ích nhất trước mắt của AI như mình là đọc nguồn, đặt giả thuyết, viết nghiên cứu, kiểm tra kế toán PnL và tìm nguyên nhân thất bại. Reviewer có nhiệm vụ phản bác kết quả, không chỉ diễn giải tại sao đường vốn đẹp. Nhiều agent không thay thế dữ liệu kiểm định độc lập.

Nếu AI tham gia tín hiệu, nên cho nó làm việc mà chỉ báo giá không làm được: đọc thông báo, phân loại sự kiện, xác định tài sản chịu tác động, phân biệt thông tin mới với bản đăng lại. Ví dụ cần khảo sát dữ liệu: thay đổi nguồn cung, niêm yết/hủy niêm yết, sự cố bảo mật và thay đổi vận hành giao thức. Đây là các ứng viên dữ liệu; chưa phải tín hiệu giao dịch đã được xác nhận.

Mỗi sự kiện cần lưu URL, nội dung/phiên bản, thời gian công bố, lần đầu hệ thống nhìn thấy, thời gian hoàn tất xử lý và giá có thể giao dịch sau đó. AI phải có trạng thái “không xác định”; confidence bằng lời không được coi là xác suất đã hiệu chuẩn. Tin tức là công khai, nên giả thuyết phải giải thích vì sao thông tin vẫn còn giá trị sau độ trễ thực tế của mình.

Về tích hợp, [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) giúp cố định cấu trúc kết quả, nhưng không đảm bảo nội dung đúng. [Function calling](https://developers.openai.com/api/docs/guides/function-calling) kết nối mô hình với công cụ/dữ liệu. [Agent evals](https://developers.openai.com/api/docs/guides/agent-evals) hỗ trợ đánh giá quy trình. Cả ba là năng lực kỹ thuật; lợi nhuận phải được đo riêng.

**Thứ tự nghiên cứu phù hợp với dự án**

| Ưu tiên | Giả thuyết cần kiểm định | Điều khiến phải loại bỏ |
|---|---|---|
| 1. Spot giao dịch chậm hơn: trend và sức mạnh tương đối | Xu hướng kéo dài đủ lâu để vượt entry delay và mọi chi phí; xếp hạng có thông tin ngoài beta thị trường. | Kết quả chỉ đến từ một đợt tăng, một coin, hoặc thua danh mục thụ động khi ghép cùng mức rủi ro. |
| 2. Sizing theo volatility và hạn chế turnover | Giảm rủi ro/chi phí mà vẫn giữ được phần lợi nhuận có ích. | “Cải thiện” chỉ do giữ nhiều cash hơn; không hơn baseline có cùng exposure/volatility. |
| 3. Feature AI từ sự kiện | Thông tin còn sức dự báo tại thời điểm có thể đặt lệnh; tốt hơn dữ liệu số và phân loại đơn giản. | Lợi thế biến mất khi thêm độ trễ, bỏ tin đăng lại, hoặc trừ chi phí AI. |
| 4. Funding/OI làm dữ liệu bổ sung | Một cơ chế định trước về crowded positioning có khả năng dự báo. | Đổi câu chuyện continuation/reversal sau khi xem kết quả; feature không có đóng góp ổn định. |
| Nhánh riêng: carry | Chênh lệch sau mọi chi phí đủ bù rủi ro vốn, collateral và hedge. | Lãi chỉ tồn tại khi giả định hai chân khớp tức thời, funding không đổi hoặc bỏ chi phí vốn. |

Volatility sizing là cách phân bổ rủi ro, không tự tạo edge. Turnover thấp chỉ giữ lại edge nếu edge đã tồn tại. OI tăng không tự cho biết hướng giá; funding cao có thể đi cùng tiếp diễn hoặc đảo chiều. Không mặc định một quan hệ chỉ vì nó dễ kể thành câu chuyện.

Đối với cấu hình thí nghiệm $50, mức phí giả định 0,1% mỗi chiều đòi hỏi một vòng mua–bán vượt khoảng 0,2% chỉ để hòa phí. Thêm $1 chi phí cố định/tháng tương đương 2% vốn/tháng. Đây là phép tính từ giả định, không phải báo giá API hay fee tier đã xác minh. Nghiên cứu offline, cache và xử lý khi có sự kiện phù hợp hơn việc gọi nhiều agent ở mỗi nến.

**Tiêu chuẩn đánh giá trước khi chọn chiến lược mới**

1. Xem toàn bộ lịch sử 2024–2026 đã khảo sát là dữ liệu phát triển. Chia lại nó không biến thành một tập kiểm định chưa từng thấy. Chốt tập ứng viên nhỏ, quy tắc và ngân sách thử nghiệm trước khi thu dữ liệu tương lai.
2. So với cash, BTC buy-and-hold, rổ cố định và danh mục thụ động có cùng exposure/rủi ro. Một chiến lược có drawdown thấp do dùng ít vốn cần được nhận diện đúng.
3. Đo đóng góp từng thành phần: quy tắc giá đơn giản → sizing → xếp hạng → feature dữ liệu mới → AI. Thử có/không có từng phần với cùng điều kiện; bỏ phần phức tạp không tạo thêm giá trị.
4. Tính phí, spread, slippage, độ trễ, lệnh tối thiểu, làm tròn và lỗi/mất dữ liệu. Chi phí AI/VPS được ghi riêng để thấy cả PnL giao dịch lẫn lãi/lỗ vận hành.
5. Chạy các kiểm tra độ nhạy: bỏ từng coin, thay tham số lân cận, tăng chi phí và trì hoãn khớp lệnh. Chúng giúp bác bỏ phương án mong manh, nhưng vẫn là kiểm tra phát triển sau khi đã chọn trên lịch sử.
6. Forward paper đối chứng, chốt model/prompt/policy cho mỗi vòng đo. Đăng ký trước thời điểm xem xét và tiêu chuẩn dừng; tránh liên tục sửa đến khi cùng một tập kiểm định trở nên có lời.
7. Đánh giá khoảng bất định có tính đến tự tương quan và tương quan giữa coin. Nhiều nến hoặc nhiều lệnh không tương đương nhiều quan sát độc lập. Một số tuần cố định không tự đủ để chứng minh lợi thế.

Tiêu chuẩn đưa một ứng viên lên bước tiếp theo là kỳ vọng ròng có bằng chứng đáng tin, drawdown chấp nhận được và hành vi thực thi nhất quán. Nếu không đạt, kết luận là chưa có chiến lược phù hợp hoặc cần loại giả thuyết đó; không bắt buộc chọn phương án ít lỗ nhất.

Báo cáo backtest trước đó vẫn là bằng chứng về các triển khai đã thử: [QUANT_RESEARCH_20260920.md](D:/Code/bot/docs/QUANT_RESEARCH_20260920.md). Khảo sát ngành này mở rộng giả thuyết và cách dùng AI; nó không đảo ngược các kết quả âm đã đo được.
