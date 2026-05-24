export type DisplayCurrency = "ILS" | "USD" | "EUR";

export const displayCurrencies: DisplayCurrency[] = ["ILS", "USD", "EUR"];

export function parseDisplayCurrency(value: unknown): DisplayCurrency {
	return typeof value === "string" &&
		displayCurrencies.includes(value as DisplayCurrency)
		? (value as DisplayCurrency)
		: "ILS";
}

export function formatUsdCost(
	value: number | null | undefined,
	currency: DisplayCurrency,
): string {
	if (value === null || value === undefined) {
		return "not billed";
	}
	const fractionDigits = Math.abs(value) < 0.01 ? 4 : 2;
	if (currency === "ILS") {
		return `₪${value.toFixed(fractionDigits)} ILS`;
	}
	return new Intl.NumberFormat(undefined, {
		style: "currency",
		currency,
		minimumFractionDigits: fractionDigits,
		maximumFractionDigits: fractionDigits,
	}).format(value);
}
