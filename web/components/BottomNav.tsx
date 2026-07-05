"use client";

export type Tab = "opportunities" | "recommendations" | "tracked" | "search";

const items: {
  id: Tab;
  label: string;
  Icon: (p: { active: boolean }) => React.ReactElement;
}[] = [
  { id: "opportunities", label: "Ranked", Icon: BarsIcon },
  { id: "recommendations", label: "Buys", Icon: BoltIcon },
  { id: "tracked", label: "Portfolio", Icon: WalletIcon },
  { id: "search", label: "Search", Icon: SearchIcon },
];

export function BottomNav({
  tab,
  onChange,
}: {
  tab: Tab;
  onChange: (t: Tab) => void;
}) {
  return (
    <nav className="bottomnav">
      <ul>
        {items.map(({ id, label, Icon }) => {
          const active = tab === id;
          return (
            <li key={id}>
              <button
                type="button"
                className={`navbtn ${active ? "active" : ""}`}
                onClick={() => onChange(id)}
                aria-current={active ? "page" : undefined}
              >
                <Icon active={active} />
                <span>{label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

type IconProps = { active: boolean };

function BarsIcon({ active }: IconProps) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.3 : 1.9} strokeLinecap="round">
      <path d="M5 20V10M12 20V4M19 20v-7" />
    </svg>
  );
}

function BoltIcon({ active }: IconProps) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.3 : 1.9} strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />
    </svg>
  );
}

function WalletIcon({ active }: IconProps) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.3 : 1.9} strokeLinejoin="round">
      <rect x="3" y="6" width="18" height="14" rx="3" />
      <path d="M3 10h18" />
      <circle cx="16.5" cy="14" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

function SearchIcon({ active }: IconProps) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.3 : 1.9} strokeLinecap="round">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}
