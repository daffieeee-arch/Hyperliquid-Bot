import type { PaperFills, PaperOrders } from "../lib/types";
import { formatGroupedNumber, yesNo } from "../lib/display";

export function PaperBlotter({ orders, fills }: { orders: PaperOrders; fills: PaperFills }) {
  return (
    <section className="panel blotter-panel">
      <div className="panel-head">
        <h2>PAPER blotter</h2>
        <p className="panel-kicker">
          Sandbox intents/fills copied from reconstructable JSON · venue orders submitted{" "}
          <span className="tone-neutral">{yesNo(orders.venue_orders_submitted)}</span>
        </p>
      </div>
      <div className="blotter-grid">
        <div className="blotter-pane">
          <h3>Intents</h3>
          {orders.intents.length === 0 ? (
            <p className="empty">No PAPER intents in this reconstructable run.</p>
          ) : (
            <table className="blotter">
              <thead>
                <tr>
                  <th>Id</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Type</th>
                  <th>Reason</th>
                  <th>Reduce</th>
                </tr>
              </thead>
              <tbody>
                {orders.intents.map((intent) => (
                  <tr key={intent.client_order_id}>
                    <td>{intent.client_order_id}</td>
                    <td className={intent.side === "BUY" ? "tone-up" : "tone-down"}>
                      {intent.side}
                    </td>
                    <td>{formatGroupedNumber(intent.quantity)}</td>
                    <td>{intent.order_type}</td>
                    <td>{intent.reason}</td>
                    <td>{yesNo(intent.reduce_only)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="blotter-pane">
          <h3>Fills</h3>
          {fills.fills.length === 0 ? (
            <p className="empty">No PAPER fills in this reconstructable run.</p>
          ) : (
            <table className="blotter">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Price</th>
                  <th>Liq</th>
                  <th>Pos after</th>
                </tr>
              </thead>
              <tbody>
                {fills.fills.map((fill) => (
                  <tr key={fill.fill_ordinal}>
                    <td>{fill.fill_ordinal}</td>
                    <td className={fill.side === "BUY" ? "tone-up" : "tone-down"}>{fill.side}</td>
                    <td>{formatGroupedNumber(fill.quantity)}</td>
                    <td>{formatGroupedNumber(fill.price)}</td>
                    <td>{fill.liquidity_side}</td>
                    <td>{formatGroupedNumber(fill.position_after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
