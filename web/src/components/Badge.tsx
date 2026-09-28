// The fixed safety badge (plan 5.10, spec section 7): shown at the top of every state of the
// page, so it is never possible to look at this app and mistake it for a trading system.
const BADGE_TEXT = "NO REAL ORDERS · portfolio read-only";

export function Badge(): JSX.Element {
  return (
    <div className="badge" data-testid="no-real-orders-badge" role="status">
      {BADGE_TEXT}
    </div>
  );
}
