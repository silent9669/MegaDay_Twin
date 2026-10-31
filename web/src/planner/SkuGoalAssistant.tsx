import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ArrowRight, MessageCircle, Search, Send } from 'lucide-react';
import type { AssistantContext, AssistantMessage, AssistantSkuList, AssistantTurnResponse } from './assistant-contract';
import type { GoalInterpretResponse } from './goal-contract';
import { SaleGoalPlanner } from './SaleGoalPlanner';
import './assistant.css';

async function request<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(`/api/sale-assistant/${path}`, { signal, cache: 'no-store',
    method: body === undefined ? 'GET' : 'POST',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) throw Error(payload?.error?.message || 'Chưa thể đọc dữ liệu từ backend. Hãy thử lại.');
  return payload as T;
}
const number = (value: number | null, money = false) => value == null ? 'Chưa có dữ liệu' : `${new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 }).format(value)}${money ? ' ₫' : ''}`;
const date = (value: string | null) => value ? new Date(value).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' }) : 'Chưa cung cấp';
const quickGoals = [
  ['Bán đủ số lượng', 'Tôi muốn bán đủ số lượng sản phẩm này trong buổi sale.'],
  ['Giải phóng tồn kho', 'Tôi muốn xả hàng tồn của sản phẩm này.'],
  ['Ưu tiên lợi nhuận', 'Tôi muốn tối ưu lợi nhuận của chiến dịch có sản phẩm này.'],
] as const;

export function SkuGoalAssistant() {
  const [params, setParams] = useSearchParams();
  const sku = params.get('sku') ?? '';
  const [query, setQuery] = useState('');
  const [list, setList] = useState<AssistantSkuList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true); setError('');
      void request<AssistantSkuList>(`skus?q=${encodeURIComponent(query.trim())}`, controller.signal)
        .then((value) => { if (!controller.signal.aborted) setList(value); })
        .catch((e: unknown) => { if (!controller.signal.aborted) { setList(null); setError(e instanceof Error ? e.message : 'Không tải được danh sách SKU.'); } })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, query ? 250 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, reload]);
  return <div className="sale-planner sku-assistant" id="sku-assistant">
    <header className="planner-heading"><div><span className="planner-eyebrow">MEGADAY HQ / TRỢ LÝ THEO SẢN PHẨM</span><h1><MessageCircle aria-hidden size={27} /> Mục tiêu cho SKU</h1><p>Chọn sản phẩm, nói điều bạn muốn đạt. Trợ lý làm rõ yêu cầu; bạn kiểm tra trước khi hệ thống tính phương án.</p></div><Link to="/city-demo?district=hq">Về HQ <ArrowRight aria-hidden size={16} /></Link></header>
    <div className="sku-assistant-layout">
      <aside className="planner-card sku-picker" aria-labelledby="sku-picker-title"><h2 id="sku-picker-title">Chọn sản phẩm</h2><label htmlFor="assistant-search"><Search aria-hidden size={16} /> Tìm SKU</label><input id="assistant-search" type="search" maxLength={150} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Mã SKU hoặc tên sản phẩm" /><p className="planner-help">Danh sách từ backend đã kết nối. Dữ liệu đến: {date(list?.dataAsOf ?? null)}.</p>
        {loading && <p role="status">Đang tìm sản phẩm…</p>}
        {error && <div role="alert"><p>{error}</p><button type="button" onClick={() => setReload((v) => v + 1)}>Thử tải lại SKU</button><Link to="/mock-data">Kiểm tra Data Studio</Link></div>}
        {!loading && !error && !list?.items.length && <p>Không có SKU phù hợp. Kiểm tra từ khóa hoặc dữ liệu đã gửi sang backend.</p>}
        <ul className="sku-picker-list" aria-label="Sản phẩm từ backend">{list?.items.map((item) => <li key={item.sku}><button type="button" aria-label={`Chọn SKU ${item.sku}`} aria-pressed={sku === item.sku} onClick={() => setParams({ sku: item.sku })}><strong>{item.name || item.sku}</strong><span>{item.sku}{item.variant ? ` · ${item.variant}` : ''}</span></button></li>)}</ul>
        {list?.nextCursor && <p className="planner-help">Còn sản phẩm khác. Nhập mã hoặc tên cụ thể để thu hẹp tìm kiếm.</p>}
      </aside>
      {sku ? <SkuConversation key={sku} sku={sku} /> : <section className="planner-card sku-assistant-empty"><MessageCircle aria-hidden size={36} /><h2>Bắt đầu từ một sản phẩm</h2><p>Chọn SKU ở danh sách để xem dữ liệu và trao đổi mục tiêu. Trợ lý sẽ hỏi thêm khi chưa đủ thông tin.</p></section>}
    </div>
  </div>;
}

function SkuConversation({ sku }: { sku: string }) {
  const [context, setContext] = useState<AssistantContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [contextError, setContextError] = useState('');
  const [reload, setReload] = useState(0);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [interpretation, setInterpretation] = useState<GoalInterpretResponse | null>(null);
  const [review, setReview] = useState(false);
  const [revision, setRevision] = useState(0);
  const active = useRef<AbortController | null>(null);
  const reviewAnchor = useRef<HTMLElement>(null);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    void request<AssistantContext>(`context?sku=${encodeURIComponent(sku)}`, controller.signal).then((value) => {
      if (value.sku.sku !== sku) throw Error('Ngữ cảnh trả về không khớp SKU đã chọn.');
      if (!controller.signal.aborted) { setContext(value); setContextError(''); }
    }).catch((e: unknown) => { if (!controller.signal.aborted) { setContext(null); setContextError(e instanceof Error ? e.message : 'Không đọc được dữ liệu SKU.'); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [sku, reload]);
  const used = messages.filter((message) => message.role === 'user').reduce((sum, message) => sum + message.content.length + 1, 0);
  const tooLong = used + input.trim().length > 3900 || messages.length >= 14;
  async function send() {
    if (!input.trim() || !context || busy || tooLong) return;
    const next: AssistantMessage[] = [...messages, { role: 'user', content: input.trim() }];
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(''); setReview(false);
    try {
      const value = await request<AssistantTurnResponse>('turn', controller.signal, { sku, messages: next });
      if (controller.signal.aborted) return;
      if (value.context.sku.sku !== sku) throw Error('Phản hồi không khớp SKU đã chọn. Hãy thử lại.');
      setMessages([...next, { role: 'assistant', content: value.reply }]); setInput('');
      setContext(value.context); setInterpretation(value.interpretation); setRevision((v) => v + 1);
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Trợ lý chưa phản hồi. Nội dung nhập được giữ lại để thử lại.'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  if (loading) return <section className="planner-card" role="status">Đang đọc dữ liệu của {sku}…</section>;
  if (!context) return <section className="planner-card" role="alert"><h2>Chưa đọc được SKU {sku}</h2><p>{contextError}</p><button type="button" onClick={() => { setLoading(true); setReload((v) => v + 1); }}>Thử tải lại ngữ cảnh</button></section>;
  return <div className="sku-conversation">
    <section className="planner-card sku-context" aria-labelledby="assistant-sku-title"><span className="planner-eyebrow">ĐANG LẬP MỤC TIÊU CHO</span><h2 id="assistant-sku-title">{context.sku.name || sku}</h2><p><strong>{sku}</strong>{context.sku.variant ? ` · ${context.sku.variant}` : ''}</p><p className="planner-help">Dữ liệu đến {date(context.dataAsOf)} (giờ Việt Nam). Bấm vào số để xem nguồn.</p>
      <div className="sku-facts">{([
        ['Tồn khả dụng', context.sku.stockAvailable, false, 'stock_available'],
        ['Đã bán hôm nay', context.sku.soldTodayUnits, false, 'sold_today_units'],
        ['Giá bán hiệu lực', context.sku.priceEffectiveVnd, true, 'price_effective_vnd'],
        ['Đóng góp mỗi đơn vị', context.sku.contributionPerUnitVnd, true, 'contribution_per_unit_vnd'],
      ] as const).map(([label, value, money, field]) => <details key={field}><summary><span>{label}</span><strong>{number(value, money)}</strong></summary><p>Trường <code>{field}</code> trong dữ liệu SKU từ backend. {context.source?.label ?? 'Backend chưa cung cấp tên nguồn.'}</p><p>Mã nguồn: {context.source?.id ?? 'Chưa cung cấp'} · mốc {date(context.source?.data_as_of ?? context.dataAsOf)}.</p>{field === 'contribution_per_unit_vnd' && <p>Chỉ số đóng góp do backend tính; chưa trừ chi quảng cáo chung. Phép tính cụ thể được lưu trong truy vết khi chạy mô phỏng.</p>}</details>)}</div>
      <div className="sku-campaign-scope"><strong>Phạm vi chiến dịch</strong>{context.campaigns.length ? <ul>{context.campaigns.map((campaign) => <li key={campaign.id}>{campaign.name} ({campaign.id}) · SKU: {campaign.skus.join(', ')}</li>)}</ul> : <p>SKU chưa thuộc chiến dịch nào. Bạn vẫn có thể làm rõ mục tiêu; cần liên kết chiến dịch trước khi mô phỏng.</p>}<p>Số lượng bán/xả hàng có thể nhắm SKU này. Ngân sách, lỗ kỳ vọng và chỉ số kinh tế J luôn thuộc toàn chiến dịch, không được coi là ngân sách hay lợi nhuận riêng của SKU.</p></div>
      {!!context.notices.length && <ul className="planner-notices">{context.notices.map((notice, i) => <li key={i}>{notice}</li>)}</ul>}
    </section>
    <section className="planner-card sku-chat" aria-labelledby="assistant-chat-title"><h2 id="assistant-chat-title">Trao đổi mục tiêu</h2><div className="sku-chat-log" role="log" aria-label="Trao đổi với trợ lý" aria-live="polite"><div className="sku-message sku-message-assistant"><span>Trợ lý</span><p>{context.opening}</p></div>{messages.map((message, index) => <div className={`sku-message sku-message-${message.role}`} key={index}><span>{message.role === 'user' ? 'Bạn' : 'Trợ lý'}</span><p>{message.content}</p></div>)}</div>
      <form onSubmit={(e) => { e.preventDefault(); void send(); }}><fieldset disabled={busy}><div className="planner-templates">{quickGoals.map(([label, text]) => <button type="button" key={label} onClick={() => { setInput(text); setReview(false); }}>{label}</button>)}</div><label htmlFor="assistant-needs">Nhu cầu của bạn</label><textarea id="assistant-needs" rows={3} maxLength={2000} value={input} onChange={(e) => { setInput(e.target.value); setReview(false); }} placeholder="Bạn muốn đạt điều gì với sản phẩm này?" /><p className="planner-help">Có thể bổ sung hoặc sửa mục tiêu qua từng lượt. Ngày giờ và các giới hạn chưa rõ sẽ được hỏi lại hoặc chỉnh ở bước kiểm tra. Gửi nội dung đang nhập trước khi kiểm tra bản nháp mới.</p>{tooLong && <p className="planner-warning">Cuộc trao đổi đã dài. Kiểm tra bản nháp hiện có hoặc bắt đầu lại với mô tả ngắn hơn.</p>}<div className="planner-actions"><button className="planner-primary" type="submit" disabled={!input.trim() || tooLong}><Send aria-hidden size={16} />{busy ? 'Đang diễn giải…' : 'Gửi'}</button>{interpretation && <button type="button" disabled={!!input.trim()} onClick={() => { setReview(true); requestAnimationFrame(() => reviewAnchor.current?.focus()); }}>Kiểm tra mục tiêu <ArrowRight aria-hidden size={16} /></button>}<button type="button" disabled={!messages.length} onClick={() => { setMessages([]); setInput(''); setInterpretation(null); setReview(false); setError(''); }}>Bắt đầu lại</button></div></fieldset></form>
      {busy && <p role="status">Trợ lý đang kiểm tra nhu cầu của SKU {sku}…</p>}{error && <p role="alert" className="planner-error">{error}</p>}
      <p className="planner-help">Trao đổi chưa lưu mục tiêu hay chạy mô phỏng. Chỉ các thông số bạn xác nhận ở bước tiếp theo mới được dùng để tính.</p>
    </section>
    {review && interpretation && <section tabIndex={-1} ref={reviewAnchor} aria-label="Kiểm tra mục tiêu theo SKU"><SaleGoalPlanner key={`${sku}-${revision}`} embedded initialInterpretation={interpretation} focusSku={sku} campaignIds={context.campaigns.map((campaign) => campaign.id)} allowRun={context.canPlan} /></section>}
  </div>;
}
