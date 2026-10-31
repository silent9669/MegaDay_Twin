export const money = (n: number | null | undefined) => n == null ? "Chưa có dữ liệu" : `${n.toLocaleString("vi-VN", { maximumFractionDigits: 0 })}đ`;
export const units = (n: number | null | undefined) => n == null ? "Chưa có dữ liệu" : n.toLocaleString("vi-VN", { maximumFractionDigits: 1 });
export const time = (s: string | null | undefined) => s ? new Date(s).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "Chưa có dữ liệu";

