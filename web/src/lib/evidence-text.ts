// Translate authored demo explanations at display time. Source payloads and values stay intact.
const explanations: Record<string, string> = {
  "Hàng NOICHIEN2L sẽ hết khoảng 14:00 11/11 (nhu cầu ~162 sp, tồn 76 sp): ads không bán thêm được khi hết hàng":
    "NOICHIEN2L runs out around 14:00 on 11/11 (demand ~162 units, stock 76 units): ads cannot sell more after stock runs out",
  "Ở giá 11.11 (531.000đ) mỗi sp NOICHIEN2L lãi 9.280đ; ads chỉ có lời khi iROAS ≥ 57.2":
    "At the 11.11 price (531,000 VND), NOICHIEN2L contributes 9,280 VND per unit; ads break even only at iROAS ≥ 57.2",
  "Không được tắt hẳn vì đã cam kết với sàn trong khung flash sale":
    "Ads cannot be fully paused during the committed flash-sale window",
  "Chỉ chạy theo khung giờ 00:00–02:00, 12:00–14:00 lợi hơn Giảm ngân sách 50% khoảng 1.217.012đ (xác suất 100%)":
    "Running 00:00–02:00 and 12:00–14:00 beats a 50% budget reduction by about 1,217,012 VND (probability 100%)",
  "Dự báo nhu cầu ở mức 'reference'": "Demand forecast confidence: reference",
  "Còn 120 sp và 4 giờ: chạy tiếp bán hết sau khoảng 3 giờ":
    "120 units and 4 hours remain: continuing ads sells out after about 3 hours",
  "Mỗi sp lãi 45.000đ, ads tốn 450.000đ mỗi giờ":
    "Each unit contributes 45,000 VND; ads cost 450,000 VND per hour",
  "Nếu tạm dừng vẫn bán khoảng 18–31 sp/giờ, ngưỡng hòa vốn 22,5 nằm giữa khoảng":
    "Pausing ads still sells around 18–31 units/hour; the break-even threshold of 22.5 falls within that range",
  "Nếu hàng dư còn giá trị (ròng, so với giá vốn) từ -199.969đ/sp trở xuống → Giảm ngân sách 50% tốt hơn":
    "At a net leftover value relative to cost of −199,969 VND/unit or lower → reducing the budget by 50% is better",
  "Nếu hàng dư còn giá trị từ 16.875đ/sp trở lên → Tạm dừng tốt hơn trên cả khoảng dự báo":
    "At a leftover value of at least 16,875 VND/unit → pausing is better across the forecast range",
  "Kết quả mô phỏng các lựa chọn": "Simulated action results",
  "Đóng góp ví dụ": "Example contribution",
  "Hòa vốn 22,5": "Break-even 22.5",
};
export function evidenceText(value: string): string {
  if (explanations[value]) return explanations[value];
  if (value.startsWith("Tạm dừng bị cấm vì đã cam kết với sàn"))
    return "Pausing is blocked by a flash-sale commitment. Financially, pausing beats continuing; the chart shows the cost of that constraint, not permission to pause.";
  return value
    .replace(/^Lợi nhuận tới /, "Profit through ")
    .replace(/^Mô phỏng /, "Simulation ")
    .replace(/^Dự báo nhu cầu /, "Demand forecast ")
    .replace(/^Dự báo nhu cầu$/, "Demand forecast")
    .replace(
      /^Giá bán, giá vốn, phí sàn của /,
      "Price, cost and platform fees for ",
    )
    .replace(/^Cam kết chương trình sale của /, "Sale commitments for ");
}
