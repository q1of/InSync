import { h } from 'preact';

type P = { size?: number; color?: string; stroke?: number };
const svg = (children: any, size = 24, color = 'currentColor', stroke = 2) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
       strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);

export const IconScan = ({ size, color, stroke }: P) => svg(<>
  <path d="M3 7V5a2 2 0 0 1 2-2h2" />
  <path d="M17 3h2a2 2 0 0 1 2 2v2" />
  <path d="M21 17v2a2 2 0 0 1-2 2h-2" />
  <path d="M7 21H5a2 2 0 0 1-2-2v-2" />
  <path d="M3 12h18" />
</>, size, color, stroke);

export const IconUsers = ({ size, color, stroke }: P) => svg(<>
  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
  <circle cx="9" cy="7" r="4" />
  <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
  <path d="M16 3.13a4 4 0 0 1 0 7.75" />
</>, size, color, stroke);

export const IconHistory = ({ size, color, stroke }: P) => svg(<>
  <path d="M3 3v5h5" />
  <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
  <path d="M12 7v5l4 2" />
</>, size, color, stroke);

export const IconUser = ({ size, color, stroke }: P) => svg(<>
  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
  <circle cx="12" cy="7" r="4" />
</>, size, color, stroke);


export const IconTrash = ({ size, color, stroke }: P) => svg(<>
  <path d="M3 6h18" />
  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
</>, size, color, stroke);

export const IconMail = ({ size, color, stroke }: P) => svg(<>
  <rect x="2" y="4" width="20" height="16" rx="2" />
  <path d="m22 7-10 6L2 7" />
</>, size, color, stroke);

export const IconPlus = ({ size, color, stroke }: P) => svg(<>
  <path d="M12 5v14M5 12h14" />
</>, size, color, stroke);

export const IconLogout = ({ size, color, stroke }: P) => svg(<>
  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
  <path d="m16 17 5-5-5-5" />
  <path d="M21 12H9" />
</>, size, color, stroke);


export const IconCheck = ({ size, color, stroke }: P) => svg(<polyline points="20 6 9 17 4 12" />, size, color, stroke);
export const IconX = ({ size, color, stroke }: P) => svg(<path d="M18 6 6 18M6 6l12 12" />, size, color, stroke);

export const IconCalendar = ({ size, color, stroke }: P) => svg(<>
  <rect x="3" y="4" width="18" height="18" rx="2" />
  <path d="M16 2v4M8 2v4M3 10h18" />
</>, size, color, stroke);

export const IconBell = ({ size, color, stroke }: P) => svg(<>
  <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
  <path d="M13.73 21a2 2 0 0 1-3.46 0" />
</>, size, color, stroke);

