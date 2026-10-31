import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { requestJSON } from "../studio/account";
import type { Envelope } from "../api/types";
import { time } from "./format";

type Archive = { uploaded: boolean; canControl: boolean; day?: string; adsLagHours?: number;
  counts?: Record<string, number> | null };
type Status = { replay_running: boolean; replay_hour: number | null; orders_until: string | null;
  ads_until: string | null; data_as_of: string | null; recompute_busy: boolean; last_error: string | null; finished: boolean };

export function ReplayPanel() {
  const [archive, setArchive] = useState<Archive | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [interval, setIntervalSeconds] = useState(20);
  const [confirmReset, setConfirmReset] = useState(false);
  const inFlight = useRef(false);
  const reload = useCallback(async () => {
    const [a, s] = await Promise.all([
      requestJSON<Archive>("/api/v1/live/replay-archive"),
      requestJSON<Envelope<Status>>("/api/v1/live/status"),
    ]);
    setArchive(a); setStatus(s.data);
  }, []);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await reload(); } catch { /* Show actionable errors on explicit upload/control. */ }
      if (!disposed) timer = setTimeout(() => void poll(), 5000);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [reload]);
  // Status marks are exclusive: after publishing 00:00–00:59, orders_until is 01:00.
  const nextHour = status?.finished || (status?.replay_hour === 23) ? 24
    : status?.replay_hour != null ? status.replay_hour + 1
      : status?.orders_until ? Number(status.orders_until.slice(0, 2)) || 24 : 0;
  async function control(command: "start" | "stop" | "jump" | "reset") {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(command === "stop" ? "Đang dừng sau giờ đang nạp…" : "Đang cập nhật replay…"); setError(""); setNotice("");
    try {
      const response = await requestJSON<Envelope<Status>>("/api/v1/live/control", {
        method: "POST", body: JSON.stringify({ command, ...(command === "start" ? { from_hour: nextHour, interval_seconds: interval } : command === "jump" ? { hour: nextHour } : {}) }),
      });
      setStatus(response.data); setConfirmReset(false);
      setNotice(command === "reset" ? "Đã bỏ dữ liệu replay; giữ lịch sử và mục tiêu. Bản tính trước cần được tính lại."
        : "Khi dữ liệu mới đã được nạp, Milo sẽ báo cập nhật. Bấm Cập nhật & đề xuất lại để tính theo mốc mới.");
    } catch (e) { setError(e instanceof Error ? e.message : "Không điều khiển được replay."); }
    finally { setBusy(""); inFlight.current = false; }
  }
  const running = !!status?.replay_running;
  const disabled = !!busy || !archive?.canControl;
  return <section className="campaign-card" id="replay">
    <h2>Replay demo</h2>
    <p><Link to="/mock-data">Tải live_11_11.zip trong Data Studio →</Link> Mỗi bước phát thêm một giờ đơn hàng và tồn kho; ads đến theo độ trễ trong kịch bản.</p>
    <p>Cần có lịch sử bán hàng, campaign và giá vốn trong backend trước. ZIP này chỉ bổ sung ngày replay.</p>
    <div className="campaign-replay-controls">
      <label>Nghỉ giữa hai giờ <select value={interval} disabled={disabled || running} onChange={e => setIntervalSeconds(Number(e.target.value))}>
        <option value={10}>10 giây</option><option value={20}>20 giây</option><option value={60}>60 giây</option>
      </select></label>
      <button disabled={disabled || !archive?.uploaded || running || nextHour >= 24} onClick={() => void control("start")}>Chạy replay</button>
      <button disabled={disabled || !running} onClick={() => void control("stop")}>Dừng replay</button>
      <button disabled={disabled || !archive?.uploaded || running || nextHour >= 24} onClick={() => void control("jump")}>Phát thêm 1 giờ</button>
      <button disabled={disabled || running} onClick={() => setConfirmReset(true)}>Đặt lại replay</button>
    </div>
    {confirmReset && <div className="campaign-banner"><p>Đưa backend về lịch sử trước replay? Các quan sát đã phát sẽ bị bỏ; mục tiêu vẫn giữ nguyên.</p>
      <button disabled={disabled} onClick={() => void control("reset")}>Xác nhận đặt lại replay</button> <button onClick={() => setConfirmReset(false)}>Hủy</button></div>}
    {!archive && <p>Chưa đọc được cấu hình replay. Kiểm tra kết nối backend.</p>}
    {archive && !archive.canControl && <p>Replay cần quyền lead và chế độ demo của backend.</p>}
    {archive?.uploaded && <p>Đã tải ngày {archive.day} · Ads trễ {archive.adsLagHours} giờ · {archive.counts?.orders?.toLocaleString("vi-VN")} dòng đơn, {archive.counts?.inventory?.toLocaleString("vi-VN")} dòng tồn kho.</p>}
    {status && <p>{running ? "Đang phát" : nextHour >= 24 ? "Đã phát hết ngày" : "Đang dừng"} · Dữ liệu đã nạp đến {time(status.data_as_of)} · Ads đến {status.ads_until ?? "chưa có dữ liệu replay"}.
      {status.recompute_busy && " Backend đang cập nhật mô hình; phép tính mới có thể phải đợi."}</p>}
    <p>Thời gian hiển thị là cận cuối: đến 10:00 nghĩa là đã có dữ liệu hết 09:59. Khoảng nghỉ tính sau khi nạp xong mỗi giờ. Đồng hồ City là luồng riêng.</p>
    {(busy || notice) && <p role="status">{busy || notice}</p>}
    {(error || status?.last_error) && <p role="alert">{error || status?.last_error}</p>}
  </section>;
}
