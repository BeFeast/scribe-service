import { describe, expect, test } from "bun:test";

import {
	convertUsdToDisplayCurrency,
	fmtDisplayCurrency,
	fmtUsd,
} from "../src/design-app/data.js";
import { formatUsdCost } from "../src/lib/currency";

describe("USD-backed display currency formatting", () => {
	test("converts canonical USD spend before formatting as ILS", () => {
		expect(convertUsdToDisplayCurrency(0.072, "ILS")).toBeCloseTo(0.2664, 6);
		expect(fmtDisplayCurrency(0.072, "ILS")).toBe("₪0.27 ILS");
		expect(formatUsdCost(0.072, "ILS")).toBe("₪0.27 ILS");
	});

	test("uses the same precision threshold for tiny converted spend", () => {
		expect(fmtDisplayCurrency(0.005, "ILS")).toBe("₪0.0185 ILS");
		expect(formatUsdCost(0.005, "ILS")).toBe("₪0.0185 ILS");
	});

	test("keeps USD display values canonical", () => {
		expect(convertUsdToDisplayCurrency(0.072, "USD")).toBe(0.072);
		expect(fmtDisplayCurrency(0.072, "USD")).toBe("$0.0720");
	});

	test("uses the runtime display currency path for fmtUsd", () => {
		expect(fmtUsd(0.072)).toBe("₪0.27 ILS");
		expect(fmtUsd(0.002)).not.toBe("₪0.0020 ILS");
	});
});
