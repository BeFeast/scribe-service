export function isAuthStatus(status: number): boolean {
	return status === 401 || status === 403;
}
