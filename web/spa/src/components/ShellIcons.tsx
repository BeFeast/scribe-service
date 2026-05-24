import type { CSSProperties, ReactNode } from "react";

type IconProps = {
	size?: number;
	children?: ReactNode;
	style?: CSSProperties;
};

function Icon({ size = 16, children, style }: IconProps) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 16 16"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.5"
			strokeLinecap="round"
			strokeLinejoin="round"
			style={style}
			aria-hidden="true"
		>
			{children}
		</svg>
	);
}

export const IconLibrary = (props: IconProps) => (
	<Icon {...props}>
		<rect x="2.5" y="2.5" width="3" height="11" rx="0.5" />
		<rect x="6.5" y="2.5" width="3" height="11" rx="0.5" />
		<path d="M10.5 3.6l2.4-.6 1.6 6.4-3 .6" />
	</Icon>
);

export const IconQueue = (props: IconProps) => (
	<Icon {...props}>
		<circle cx="8" cy="8" r="5.5" />
		<path d="M8 5v3.2L9.8 10" />
	</Icon>
);

export const IconOps = (props: IconProps) => (
	<Icon {...props}>
		<path d="M2 13l3-3 2.5 2.5L11 7l3 3" />
		<path d="M2 13h12" />
	</Icon>
);

export const IconSettings = (props: IconProps) => (
	<Icon {...props}>
		<circle cx="8" cy="8" r="1.8" />
		<path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />
	</Icon>
);

export const IconSearch = (props: IconProps) => (
	<Icon {...props}>
		<circle cx="7" cy="7" r="4.5" />
		<path d="M10.5 10.5l3 3" />
	</Icon>
);

export const IconPlus = (props: IconProps) => (
	<Icon {...props}>
		<path d="M8 3v10M3 8h10" />
	</Icon>
);

export const IconTable = (props: IconProps) => (
	<Icon {...props}>
		<rect x="2.5" y="3.5" width="11" height="9" rx="0.5" />
		<path d="M2.5 7h11M6 7v5.5" />
	</Icon>
);

export const IconFeed = (props: IconProps) => (
	<Icon {...props}>
		<path d="M3 4h10M3 8h10M3 12h7" />
	</Icon>
);

export const IconCards = (props: IconProps) => (
	<Icon {...props}>
		<rect x="2.5" y="2.5" width="5" height="5" rx="0.5" />
		<rect x="8.5" y="2.5" width="5" height="5" rx="0.5" />
		<rect x="2.5" y="8.5" width="5" height="5" rx="0.5" />
		<rect x="8.5" y="8.5" width="5" height="5" rx="0.5" />
	</Icon>
);

export const IconSun = (props: IconProps) => (
	<Icon {...props}>
		<circle cx="8" cy="8" r="2.5" />
		<path d="M8 1.5v1.5M8 13v1.5M1.5 8h1.5M13 8h1.5M3.5 3.5l1 1M11.5 11.5l1 1M3.5 12.5l1-1M11.5 4.5l1-1" />
	</Icon>
);

export const IconMoon = (props: IconProps) => (
	<Icon {...props}>
		<path d="M13 9.5A5.5 5.5 0 116.5 3a4.5 4.5 0 006.5 6.5z" />
	</Icon>
);

export const IconRSS = (props: IconProps) => (
	<Icon {...props}>
		<circle cx="4" cy="12" r="1" />
		<path d="M3 8a5 5 0 015 5M3 4a9 9 0 019 9" />
	</Icon>
);
