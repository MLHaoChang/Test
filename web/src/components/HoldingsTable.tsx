import type { HoldingRecord } from "../api";
import { formatMoney, formatQuantity } from "../format";
import { nameLabel } from "../labels";

export interface HoldingsTableProps {
  holdings: HoldingRecord[];
  currency: string;
}

/**
 * The holdings table after accept (plan 5.10): ISIN, name, quantity, cost, value in EUR, mapping
 * symbol and status, and flags.
 */
export function HoldingsTable({ holdings, currency }: HoldingsTableProps): JSX.Element {
  return (
    <div className="table-scroll">
      <table data-testid="holdings-table">
        <caption>Your holdings</caption>
        <thead>
          <tr>
            <th>ISIN</th>
            <th>Name</th>
            <th>Quantity</th>
            <th>Cost</th>
            <th>Value</th>
            <th>Price source</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((holding) => (
            <tr key={holding.isin} data-testid={`holdings-row-${holding.isin}`}>
              <td>{holding.isin}</td>
              <td>{nameLabel(holding.isin, holding.name)}</td>
              <td>{formatQuantity(holding.quantity)}</td>
              <td>{formatMoney(holding.cost_eur, currency)}</td>
              <td>{formatMoney(holding.value_eur, currency)}</td>
              <td>
                {holding.mapping.symbol
                  ? `${holding.mapping.symbol} (${holding.mapping.status})`
                  : holding.mapping.status}
              </td>
              <td>
                {holding.flags.length > 0 ? (
                  <ul className="flag-list">
                    {holding.flags.map((flag) => (
                      <li key={flag.kind} title={flag.detail}>
                        {flag.kind.replace(/_/g, " ")}
                      </li>
                    ))}
                  </ul>
                ) : (
                  "-"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
