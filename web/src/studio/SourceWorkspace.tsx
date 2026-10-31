import { Button } from "../components/ui/button";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { inspect, normalize, type SourcePack } from "./sources";
import { sourceView } from "./source-view";
import "./studio.css";

/** Counts and raw records from the saved account, without fixture economics or inferred SKU joins. */
export function SourceWorkspace({ pack, district }: { pack: SourcePack | null; district: "hq" | "residential" }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const feed = useMemo(() => sourceView(pack, 24), [pack]);
  const orders = useMemo(() => {
    const source = pack?.files.find(file => file.kind === "orders");
    return source ? inspect(source.name, source.text) : null;
  }, [pack]);
  const tableCounts = useMemo(() => pack?.files.map(file => ({ name: file.name, kind: file.kind, count: inspect(file.name, file.text).rows.length })) ?? [], [pack]);
  const headers = ["Mã đơn hàng", "Ngày đặt hàng", "Tên sản phẩm", "Số lượng", "Trạng thái đơn hàng"];
  const column = (name: string) => orders?.columns.find(key => normalize(key) === normalize(name)) ?? name;
  const rows = (orders?.rows ?? []).filter(row => headers.some(header => String(row[column(header)] ?? "").toLowerCase().includes(search.toLowerCase())));
  const pages = Math.max(1, Math.ceil(rows.length / 30));
  const currentPage = Math.min(page, pages - 1);
  return <section className="source-workspace">
    <header><h3>{district === "hq" ? "Workspace overview" : "Order records"}</h3><Button variant="outline" render={<Link to="/mock-data" />}>Data Studio</Button></header>
    {!pack?.files.length ? <p>Import your data in Data Studio to begin.</p> : district === "hq" ? <>
      <dl className="source-metrics">
        <div><dt>Imported tables</dt><dd>{pack.files.length}</dd></div>
        <div><dt>Order records</dt><dd>{feed.orderRows.toLocaleString()}</dd></div>
        <div><dt>Products in stock</dt><dd>{feed.stock.filter(row => (row.stock ?? 0) > 0).length.toLocaleString()}</dd></div>
        <div><dt>Ad records</dt><dd>{feed.adRows.toLocaleString()}</dd></div>
      </dl>
      <table><thead><tr><th>Table</th><th>Records</th></tr></thead><tbody>{tableCounts.map(file => <tr key={`${file.kind}:${file.name}`}><td>{file.name}</td><td>{file.count.toLocaleString()}</td></tr>)}</tbody></table>
    </> : !orders ? <p>No order table imported.</p> : <>
      <label className="product-label-search">Search orders<input value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} placeholder="Order, product or status" /></label>
      <div className="warehouse-table" role="region" aria-label="Imported orders" tabIndex={0}><table><thead><tr>{headers.map(header => <th key={header}>{header}</th>)}</tr></thead>
        <tbody>{rows.slice(currentPage * 30, (currentPage + 1) * 30).map((row, index) => <tr key={index}>{headers.map(header => <td key={header}>{String(row[column(header)] ?? "") || "—"}</td>)}</tr>)}</tbody></table></div>
      <nav className="product-label-pagination" aria-label="Order pages"><Button variant="outline" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous</Button><span>{rows.length.toLocaleString()} records · {currentPage + 1} / {pages}</span><Button variant="outline" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>Next</Button></nav>
    </>}
  </section>;
}
