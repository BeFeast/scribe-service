export type DisplayCurrency = "USD" | "EUR" | "ILS" | "GBP";

export const displayCurrencies: DisplayCurrency[] = ["USD", "EUR", "ILS", "GBP"];

const usdRates: Record<DisplayCurrency, number> = {
	USD: 1,
	EUR: 0.92,
	ILS: 3.72,
	GBP: 0.79,
};

export function parseDisplayCurrency(value: unknown): DisplayCurrency {
	return typeof value === "string" && displayCurrencies.includes(value as DisplayCurrency)
		? (value as DisplayCurrency)
		: "USD";
}

export function formatUsdCost(
	value: number | null | undefined,
	currency: DisplayCurrency,
): string {
	if (value === null || value === undefined) {
		return "cost n/a";
	}
	const converted = value * usdRates[currency];
	const fractionDigits = converted < 0.01 ? 4 : 2;
	return new Intl.NumberFormat(undefined, {
		style: "currency",
		currency,
		minimumFractionDigits: fractionDigits,
		maximumFractionDigits: fractionDigits,
	}).format(converted);
}
