# Day 26B design system

TCCP uses two coordinated workspaces: a cashier workstation and a management
console. Existing Flask routes, Jinja inheritance and services remain the foundation.
No framework, image upload, payment provider or second calculation engine is added.

## Tokens and typography

`static/css/design-system.css` defines shared controls, spacing, accessibility and
print conventions. `static/css/console.css` refines those tokens for the commercial
console. `static/css/pos.css` owns checkout-specific layout and the Designer.

| Token | Value / purpose |
| --- | --- |
| `--page-bg` | `#f3f6fb`, neutral application canvas |
| `--sidebar-bg` | `#101d31`, navy workspace navigation |
| `--text-primary` | `#17243b`, primary content |
| `--text-secondary` | `#526178`, supporting text |
| `--blue`, `--blue-dark` | `#2563eb`, `#1d4ed8`, primary actions |
| `--surface`, `--surface-muted` | White and subdued neutral surfaces |
| `--green/amber/red` and soft counterparts | Existing accessible semantic pairs |
| `--space-1` through `--space-7` | 4, 8, 12, 16, 24, 32, 48px |
| `--radius-sm/md/lg` | 8, 12, 16px |
| `--sidebar-width` | 232px; 104px in compact navigation |
| `--control-height` | 44px; charge action at least 54px |
| `--font-family` | Local system/Inter fallback stack; no external font request |
| `--shadow-card/raised` | Subtle surface and contextual elevation |
| `--transition-fast` | 150ms; reduced motion disables transitions |

Use text labels alongside severity colors. Currency remains server-formatted from
integer cents. Large numbers use tabular figures; names wrap and catalog subtitles
truncate without becoming the sole accessible name.

## Shared components

`templates/components/ui.html` contains navigation, page header, KPI, empty-state
and error-summary macros. Existing form, panel, table, badge, pagination, receipt,
confirmation and notification classes remain compatible. Customer, supplier,
purchasing, expense and financial tables now use the same `data-table` contract.
Scrollable table regions are keyboard focusable; column headers retain scope.

Ordinary success flashes use dismissible fixed toasts and status announcements.
They remain until dismissed so messages are not lost to short timers. Validation
errors remain inline. Checkout uncertainty remains persistent next to payment,
never a transient toast. Existing native confirmation dialogs retain focus restoration
and fallback confirmation. Contextual checkout controls use native details/summary,
so keyboard operation and progressive enhancement need no custom focus trap.

## POS workstation

The business sidebar and management header are hidden on checkout. The POS toolbar
shows the store, preset, simulated-payment identity and working shortcuts. Category
navigation, the product catalog and the cart occupy separate regions. A tile's main
button adds one through the existing authorized POST. A separate expandable quantity
control supports batch selection. Generic diamond placeholders avoid fake images
and letter-based product branding. Product names, price and stock remain prominent.

Customer search opens contextually; successful selection collapses it. Notes and
supported discounts remain available in compact expandable sections. The item list
scrolls independently. A sticky summary uses the authoritative subtotal, discount,
tax and total. Charge expands a simulated payment panel; the existing server cash
quote, confirmation, idempotency, uncertainty recovery and receipt navigation remain.
Reload recovery opens the payment panel so critical uncertainty is visible.

Hybrid, Quick and Custom keep persisted Day 26A semantics: density, favorites,
product/category order, allowlisted colors, placeholder and category visibility.
The Designer separates editable configuration from a sticky matching tile preview;
dragging and keyboard/touch move controls use the original revision-controlled save.

## Console and analytics

Navigation groups Overview, Sales, Inventory, Purchasing, Finance and Workspace.
Compact navigation keeps text labels. On smaller screens the existing native menu
opens the navigation without covering content. Quick actions are real authorized
route links, rather than a decorative global-search input.

Dashboard period controls reuse established filter validation. KPI definitions,
refund dates, expenses, purchasing and incomplete profit estimates come from
`financial_report`. Sales charts use actual daily series, an accessible exact-value
table and keyboard-operable date inspection. The SVG is responsive and has a text
description. It does not calculate money. Empty periods have an explicit empty state;
comparison percentages are unavailable when the prior baseline is not positive.
Current inventory alerts and latest-five transactions are labeled separately from
period-based metrics. Existing dashboard visibility preferences remain effective.

## Responsive and accessibility conventions

- Above 1100px: category/catalog/cart workstation; business sidebar remains visible.
- 768–1100px: category strip above catalog and cart, bounded viewport layout.
- Below 768px: single-column catalog and expandable cart with a fixed cart shortcut.
- Console menu adapts below 1024px; cards and forms reflow below 768px.
- Tables scroll within labeled regions rather than widening the page.
- Native inputs have labels, focus indicators, clear errors and touch-sized actions.
- F2 focuses POS search; Alt+C opens and focuses the cart.
- Print removes navigation, toolbars, transient feedback and non-receipt actions.

Breakpoints are CSS media queries (custom properties cannot control media queries).
Browser screenshot review remains a separate gate; DOM dimensions and passing tests
do not establish visual quality.
