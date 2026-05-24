export type DisplayCurrency = "ILS" | "USD" | "EUR";

export const displayCurrencies: DisplayCurrency[] = ["ILS", "USD", "EUR"];

export const usdDisplayRates: Record<DisplayCurrency, number> = {
	ILS: 3.7,
	USD: 1,
	EUR: 0.92,
};

export function parseDisplayCurrency(value: unknown): DisplayCurrency {
	return typeof value === "string" &&
		displayCurrencies.includes(value as DisplayCurrency)
		? (value as DisplayCurrency)
		: "ILS";
}

export function convertUsdToDisplayCurrency(
	value: number,
	currency: DisplayCurrency,
): number {
	return value * usdDisplayRates[currency];
}

export function formatUsdCost(
	value: number | null | undefined,
	currency: DisplayCurrency,
): string {
	if (value === null || value === undefined) {
		return "not billed";
	}
	const converted = convertUsdToDisplayCurrency(value, currency);
	const fractionDigits = Math.abs(converted) < 0.01 ? 4 : 2;
	if (currency === "ILS") {
		return `₪${converted.toFixed(fractionDigits)} ILS`;
	}
	return new Intl.NumberFormat(undefined, {
		style: "currency",
		currency,
		minimumFractionDigits: fractionDigits,
		maximumFractionDigits: fractionDigits,
	}).format(converted);
}
